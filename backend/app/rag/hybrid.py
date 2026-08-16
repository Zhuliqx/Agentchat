"""混合检索：向量（Milvus）+ BM25（Postgres 文本）+ RRF 融合。

两路独立检索（语义 + 关键词），用 RRF（Reciprocal Rank Fusion）融合排序，
兼顾\"语义相近但无关键词\"与\"术语/专名精确命中\"两类召回。
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.db.models import Document
from app.db.postgres import SessionLocal
from app.rag import vector_store
from app.rag.bm25 import BM25Index

# 文档集签名的短 TTL 缓存（按用户隔离）：避免每次检索都查一次 COUNT/MAX（摄入后主动失效）。
_SIGNATURE_TTL = 5.0
_signature_cache: dict[str, dict[str, Any]] = {}  # user_id -> {"ts": float, "value": tuple}


def _docs_signature(user_id: str = "default") -> tuple:
    """文档集签名：该用户的行数 + 最新创建时间（TTL 缓存），用于 BM25 索引失效判断。"""
    now = time.monotonic()
    entry = _signature_cache.get(user_id)
    if entry is not None and now - entry["ts"] < _SIGNATURE_TTL:
        return entry["value"]
    with SessionLocal() as db:
        count, latest = db.execute(
            select(func.count(), func.max(Document.created_at)).select_from(Document).where(
                Document.user_id == user_id
            )
        ).one()
    value = (count or 0, latest)
    _signature_cache[user_id] = {"ts": now, "value": value}
    return value


def invalidate_docs_signature() -> None:
    """文档变化（摄入/删除）后立即失效签名缓存，使 BM25 索引重建。"""
    _signature_cache.clear()


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
    # 按用户隔离的向量过滤表达式
    filter_expr = None
    if user_id:
        uid = user_id.replace('"', '\\"')
        filter_expr = f'user_id == "{uid}"'

    # 1. 向量通道（Milvus 语义检索，限定用户）
    dense = vector_store.search(
        query, top_k=top_k * 3, score_threshold=threshold, filter_expr=filter_expr
    )

    # 2. BM25 关键词通道（该用户的 Postgres 文本）
    #    文档块数超过 bm25_max_docs 时跳过，避免全量建索引的内存/CPU 开销
    bm25_hits: list[tuple[int, float]] = []
    signature = _docs_signature(user_id)
    if signature[0] and signature[0] <= settings.bm25_max_docs:
        index, docs = _bm25_index(signature, user_id)
        bm25_hits = index.search(query, top_k=settings.hybrid_candidate_k)
    else:
        docs: list[dict] = []

    # 3. 构建两路排名（融合 id 空间）
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

    # 4. RRF 融合
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
    return results
