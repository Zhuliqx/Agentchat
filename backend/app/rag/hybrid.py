"""混合检索：向量（Milvus）+ BM25（Postgres 文本）+ RRF 融合。

两路独立检索（语义 + 关键词），用 RRF（Reciprocal Rank Fusion）融合排序，
兼顾\"语义相近但无关键词\"与\"术语/专名精确命中\"两类召回。
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select

from app.cache.redis_client import get_redis, redis_key
from app.config import settings
from app.db.models import Document
from app.db.postgres import SessionLocal
from app.observability import record_retrieval_stats
from app.rag import vector_store
from app.rag.bm25 import BM25Index

logger = logging.getLogger(__name__)

# 向量通道与 BM25 通道并行检索的共享线程池（search_hybrid 常在
# asyncio.to_thread 内被调用，这里再并发两个纯读通道，缩短检索延迟）
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="rag-hybrid"
)

# 文档集签名的短 TTL 缓存（按用户隔离）：避免每次检索都查一次 COUNT/MAX（摄入后主动失效）。
_SIGNATURE_TTL = 5.0
_signature_cache: dict[str, dict[str, Any]] = {}  # Redis 不可用时的进程内回退
_signature_lock = threading.Lock()  # 双通道并行后 _docs_signature 可能被多线程并发访问


def _signature_key(user_id: str) -> str:
    """文档集签名在 Redis 中的 key（带统一前缀与业务域）。"""
    return redis_key("rag", "sig", user_id)


def _encode_signature(value: tuple) -> str:
    """签名序列化为 JSON 数组：行数 + 最新创建时间（无行时为 null）。"""
    count, latest = value
    return json.dumps([int(count), latest.isoformat() if latest else None])


def _decode_signature(raw: str | bytes) -> tuple:
    count, latest = json.loads(raw)
    return int(count), datetime.fromisoformat(latest) if latest else None


def _remember_signature(user_id: str, value: tuple) -> None:
    """写入本进程签名缓存（Redis 不可用时的回退层）。"""
    with _signature_lock:
        _signature_cache[user_id] = {"ts": time.monotonic(), "value": value}


def _forget_signature(user_id: str) -> None:
    """删除本进程与 Redis 中的签名缓存。"""
    with _signature_lock:
        _signature_cache.pop(user_id, None)
    client = get_redis()
    if client is not None:
        try:
            client.delete(_signature_key(user_id))
        except Exception:  # noqa: BLE001
            pass  # Redis 故障时只清内存；5 秒 TTL 也会自动淘汰旧 key


def _query_docs_signature(user_id: str) -> tuple:
    """从 Postgres 计算文档集签名（调用方负责缓存）。"""
    with SessionLocal() as db:
        count, latest = db.execute(
            select(func.count(), func.max(Document.created_at)).select_from(Document).where(
                Document.user_id == user_id
            )
        ).one()
    return (count or 0, latest)


def _docs_signature(user_id: str = "default") -> tuple:
    """文档集签名：该用户行数 + 最新创建时间；Redis 优先，用于跨 worker 共享。"""
    key = _signature_key(user_id)
    client = get_redis()
    if client is not None:
        try:
            raw = client.get(key)
        except Exception:  # noqa: BLE001
            pass  # Redis 故障时走进程内缓存，避免检索失败
        else:
            if raw is not None:
                return _decode_signature(raw)
            value = _query_docs_signature(user_id)
            try:
                client.set(key, _encode_signature(value), ex=int(_SIGNATURE_TTL))
            except Exception:  # noqa: BLE001
                pass
            return value

    # Redis 未启用或不可用：使用/填充进程内缓存
    now = time.monotonic()
    with _signature_lock:
        entry = _signature_cache.get(user_id)
        if entry is not None and now - entry["ts"] < _SIGNATURE_TTL:
            return entry["value"]
    value = _query_docs_signature(user_id)
    _remember_signature(user_id, value)
    return value


def invalidate_docs_signature(user_id: str) -> None:
    """该用户文档变化（摄入/删除）后失效签名，使 BM25 索引及时重建。"""
    _forget_signature(user_id)


@lru_cache(maxsize=16)
def _bm25_index(signature: tuple, user_id: str = "default") -> tuple[BM25Index, list[dict]]:
    """按签名 + 用户缓存 BM25 索引与对应行元数据（doc_id / chunk_index）。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(Document.id, Document.text, Document.chunk_index, Document.source)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.asc())
        ).all()
    docs = [
        {"id": r[0], "text": r[1], "chunk_index": r[2], "source": r[3]} for r in rows
    ]
    index = BM25Index(
        [d["text"] for d in docs], k1=settings.bm25_k1, b=settings.bm25_b
    )
    return index, docs


def _fusion_id(doc_id: str, chunk_index: int) -> str:
    return f"{doc_id}:{chunk_index}"


def _rrf(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：合并多路排名，交集项获得多路加分。"""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def search_hybrid(
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
    user_id: str = "default",
) -> list[dict]:
    """向量 + BM25 + RRF 融合检索（限定用户知识库），返回与 vector_store.search 同构的命中列表。"""
    top_k = top_k or settings.rag_top_k
    threshold = (
        score_threshold
        if score_threshold is not None
        else settings.rag_score_threshold
    )
    _t0 = time.perf_counter()
    # 按用户隔离的向量过滤表达式
    filter_expr = vector_store.user_filter_expr(user_id) or None

    # 两路纯读通道并行检索，缩短检索延迟
    def _run_dense() -> list[dict]:
        return vector_store.search(
            query, top_k=top_k * 3, score_threshold=threshold, filter_expr=filter_expr
        )

    def _run_bm25() -> tuple[list[tuple[int, float]], list[dict]]:
        # 文档块数超过 bm25_max_docs 时跳过，避免全量建索引的内存/CPU 开销
        signature = _docs_signature(user_id)
        if signature[0] and signature[0] <= settings.bm25_max_docs:
            index, docs_local = _bm25_index(signature, user_id)
            return index.search(query, top_k=settings.hybrid_candidate_k), docs_local
        return [], []

    f_dense = _EXECUTOR.submit(_run_dense)
    f_bm25 = _EXECUTOR.submit(_run_bm25)
    # 两个 future 都取结果：任一通道异常都向上抛出（dense 优先），
    # 且不会因 dense 失败而吞掉 bm25 侧的异常。
    dense_exc = bm25_exc = None
    try:
        dense = f_dense.result()
    except BaseException as exc:  # noqa: BLE001 - 统一收集后按原优先级抛出
        dense_exc = exc
    try:
        bm25_hits, docs = f_bm25.result()
    except BaseException as exc:  # noqa: BLE001
        bm25_exc = exc
    if dense_exc is not None:
        raise dense_exc
    if bm25_exc is not None:
        raise bm25_exc

    # 构建两路排名（融合 id 空间）
    dense_list: list[str] = []
    dense_meta: dict[str, dict] = {}
    for h in dense:
        fid = _fusion_id(str(h.get("doc_id") or ""), int(h.get("chunk_index") or 0))
        dense_list.append(fid)
        dense_meta[fid] = h

    bm25_list: list[str] = []
    bm25_meta: dict[str, dict] = {}
    for row_idx, score in bm25_hits:
        d = docs[row_idx]
        fid = _fusion_id(d["id"], d["chunk_index"])
        bm25_list.append(fid)
        bm25_meta[fid] = {
            "text": d["text"],
            "source": d["source"],
            "bm25_score": round(score, 4),
        }

    ranked_lists = [dense_list]
    if bm25_list:
        ranked_lists.append(bm25_list)

    # RRF 融合
    fused = _rrf(ranked_lists, k=settings.rrf_k)[:top_k]

    results: list[dict] = []
    for fid, rrf_score in fused:
        if fid in dense_meta:
            item = dict(dense_meta[fid])
            item["rrf_score"] = round(rrf_score, 4)
        else:
            doc_id, _, chunk_str = fid.partition(":")
            item = {
                "doc_id": doc_id,
                "chunk_index": int(chunk_str),
                "text": bm25_meta[fid]["text"],
                "metadata": {},
                # 纯 BM25 命中的块补全来源，保证与向量命中同构
                "source": bm25_meta[fid]["source"],
                "bm25_score": bm25_meta[fid]["bm25_score"],
                "rrf_score": round(rrf_score, 4),
            }
        results.append(item)

    # 记录通道命中数与耗时（观测内部自带 fail-open，不影响检索）
    record_retrieval_stats(
        "hybrid",
        {
            "user": user_id,
            "dense_hits": len(dense),
            "bm25_hits": len(bm25_hits),
            "fused": len(results),
            "top_k": top_k,
            "threshold": threshold,
        },
        (time.perf_counter() - _t0) * 1000,
    )
    return results
