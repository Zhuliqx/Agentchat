"""Milvus 向量库封装（MilvusClient 新 API，单例连接）。

基于 MilvusClient 一套 API（避免 3.0 弃用的 Collection/connections），
全局单例连接，消除每次调用新建客户端的资源泄漏。
"""
from __future__ import annotations

import json
import logging
import uuid
from functools import lru_cache
from typing import Any, Optional, cast

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from app.config import settings
from app.rag.embedding import embed_query_cached, get_embedder

logger = logging.getLogger(__name__)


@lru_cache
def _client() -> MilvusClient:
    """全局单例 MilvusClient（连接复用，杜绝泄漏）。"""
    return MilvusClient(uri=settings.milvus_connection_uri)


def user_filter_expr(user_id: str | None) -> str:
    """按用户隔离的 Milvus 过滤表达式片段（空 user_id 返回空串）。"""
    if not user_id:
        return ""
    uid = user_id.replace('"', '\\"')
    return f'user_id == "{uid}"'


def _build_schema() -> CollectionSchema:
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=32),
        FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=32),  # 对应 Postgres documents.id
        FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),  # 知识库归属用户(隔离)
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=500),
        FieldSchema(name="chunk_index", dtype=DataType.INT64),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8000),
        FieldSchema(name="metadata_json", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=settings.embedding_dim,
        ),
    ]
    return CollectionSchema(fields=fields, description="RAG 文档块向量", enable_dynamic_field=False)


def _has_index(client: MilvusClient, name: str, field_name: str) -> bool:
    """判断某字段是否已有索引。"""
    try:
        return bool(client.list_indexes(name, field_name=field_name))
    except Exception:
        return False


def _ensure_indexes(client: MilvusClient, name: str) -> None:
    """确保向量索引 + 标量索引存在（缺失则创建，幂等）。

    - embedding：IVF_FLAT / IP（相似度检索）
    - source / user_id：Trie 标量索引（加速按来源/用户的过滤与删除）
    """
    if not _has_index(client, name, "embedding"):
        ip = client.prepare_index_params()
        ip.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type=settings.milvus_metric_type,
            params={"nlist": 128},
            index_name="embedding_idx",
        )
        client.create_index(name, ip)
    for field in ("source", "user_id"):
        if not _has_index(client, name, field):
            ip = client.prepare_index_params()
            ip.add_index(field_name=field, index_type="Trie", index_name=f"{field}_idx")
            client.create_index(name, ip)


def _validate_embedding_dim(client: MilvusClient, name: str) -> None:
    """校验已有 collection 的 embedding 维度与配置一致（防静默错配）。"""
    try:
        schema = cast(dict, client.describe_collection(name))
        for f in schema.get("fields", []):
            if f.get("name") != "embedding":
                continue
            dim = (f.get("params") or {}).get("dim")
            if dim is not None and int(dim) != int(settings.embedding_dim):
                logger.warning(
                    "embedding 维度不匹配：collection='%s' 配置=%s 实际=%s。"
                    "请检查 .env 的 EMBEDDING_DIM，或删除重建 collection 后重新摄入文档",
                    name, settings.embedding_dim, dim,
                )
            return
    except Exception as exc:  # pragma: no cover
        logger.warning("校验 embedding 维度失败: %s", exc)


def ensure_vector_store() -> None:
    """确保 collection 存在（不存在则创建），并建立向量/标量索引与加载。"""
    client = _client()
    name = settings.milvus_collection
    if not client.has_collection(name):
        client.create_collection(name, schema=_build_schema())
        _ensure_indexes(client, name)
        client.load_collection(name)
        logger.info("collection '%s' 已创建", name)
    else:
        _validate_embedding_dim(client, name)  # 维度一致性校验
        _ensure_indexes(client, name)  # 幂等补齐索引（含 source 标量索引）
        client.load_collection(name)


def add_chunks(
    chunks: list[dict[str, Any]],
    *,
    doc_ids: list[str],
    source: str,
    user_id: str = "default",
    vectors: Optional[list[list[float]]] = None,
    chunk_indexes: Optional[list[int]] = None,
) -> list[str]:
    """批量插入文档块。

    chunks: [{"text": "...", "metadata": {...}}]
    doc_ids: 与 chunks 等长，每个 chunk 对应的 Postgres Document.id
    vectors: 可选，预计算嵌入向量（已嵌入时传入，避免重复计算）
    chunk_indexes: 可选，与 chunks 等长的原始块序号，缺省用批内位置；
        增量摄入必须传原始序号，保证向量通道与 Postgres（BM25 通道）的
        ``chunk_index`` 一致——融合键 ``doc_id:chunk_index`` 失配会破坏
        交集加分与相邻块合并。
    返回插入的向量主键 id 列表。
    """
    if len(doc_ids) != len(chunks):
        raise ValueError("doc_ids 与 chunks 长度不一致")
    if chunk_indexes is not None and len(chunk_indexes) != len(chunks):
        raise ValueError("chunk_indexes 与 chunks 长度不一致")

    if vectors is None:
        embedder = get_embedder()
        vectors = embedder.embed_texts([c["text"] for c in chunks])

    rows: list[dict[str, Any]] = []
    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        rows.append(
            {
                "id": uuid.uuid4().hex,
                "doc_id": doc_ids[i],
                "user_id": user_id,
                "source": source,
                "chunk_index": chunk_indexes[i] if chunk_indexes is not None else i,
                "text": chunk["text"],
                "metadata_json": json.dumps(chunk.get("metadata") or {}, ensure_ascii=False),
                "embedding": vec,
            }
        )

    _client().insert(settings.milvus_collection, rows)
    return [r["id"] for r in rows]


def sync_chunks(
    chunks: list[dict[str, Any]],
    *,
    doc_ids: list[str],
    source: str,
    user_id: str = "default",
    vectors: Optional[list[list[float]]] = None,
    chunk_indexes: Optional[list[int]] = None,
) -> list[str]:
    """幂等同步一批块：先按 doc_id 删除旧行，再插入（等于按 doc_id 的 upsert）。

    对账任务 / 重试场景复用：同一批 doc_id 重复执行不会产生重复向量或幽灵残留
    （Milvus 主键 id 是独立 uuid4，靠 doc_id 过滤保证幂等）。
    """
    if not doc_ids:
        return []
    delete_by_ids(doc_ids)
    return add_chunks(
        chunks,
        doc_ids=doc_ids,
        source=source,
        user_id=user_id,
        vectors=vectors,
        chunk_indexes=chunk_indexes,
    )


def delete_by_source(source: str, user_id: str | None = None) -> None:
    """按 source（可限定用户）删除该文档的所有向量。

    注意：Milvus 表达式语法中反斜杠与双引号需转义（Windows 路径含 \\）。
    user_id 为 None 时删除全部用户的该 source（兼容旧调用）。
    """
    escaped = source.replace("\\", "\\\\").replace('"', '\\"')
    expr = f'source == "{escaped}"'
    uf = user_filter_expr(user_id)
    if uf:
        expr += f" and {uf}"
    _client().delete(settings.milvus_collection, filter=expr)


def delete_by_ids(ids: list[str]) -> None:
    """按 Postgres 文档 id（Milvus 的 doc_id 字段）批量删除向量。

    增量摄入时清理已消失的块：Milvus 行的主键 ``id`` 是独立的 uuid4，
    与 Postgres ``documents.id`` 的对应关系在 ``doc_id`` 字段——必须按
    ``doc_id`` 过滤，否则过滤条件永远匹配不到行（幽灵向量残留）。
    """
    if not ids:
        return
    quoted = ", ".join(f'"{i}"' for i in ids)
    _client().delete(settings.milvus_collection, filter=f"doc_id in [{quoted}]")


def query_source_pairs(
    source: str, user_id: str | None = None
) -> list[tuple[str, int]]:
    """查询某 source（可限定用户）在 Milvus 中的 (doc_id, chunk_index) 集合。

    供对账任务与集成测试使用：与 Postgres documents 行做双向 diff。
    """
    escaped = source.replace("\\", "\\\\").replace('"', '\\"')
    expr = f'source == "{escaped}"'
    uf = user_filter_expr(user_id)
    if uf:
        expr += f" and {uf}"
    res = _client().query(
        settings.milvus_collection,
        filter=expr,
        output_fields=["doc_id", "chunk_index"],
    )
    return [(r.get("doc_id"), int(r.get("chunk_index"))) for r in res]


def search(
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
    filter_expr: str | None = None,
) -> list[dict[str, Any]]:
    """语义检索，返回排序后的命中文档块。"""
    query_vec = list(embed_query_cached(query))

    top_k = top_k or settings.rag_top_k
    score_threshold = score_threshold if score_threshold is not None else settings.rag_score_threshold

    results = _client().search(
        settings.milvus_collection,
        data=[query_vec],
        anns_field="embedding",
        search_params={"metric_type": settings.milvus_metric_type, "params": {"nprobe": 16}},
        limit=max(top_k * 4, 32),  # 先取多些，再按阈值过滤（避免过滤后不足 top_k）
        output_fields=["id", "doc_id", "source", "chunk_index", "text", "metadata_json"],
        filter=filter_expr or "",
    )

    hits: list[dict[str, Any]] = []
    for hit in (results[0] if results else []):
        try:
            if float(hit["distance"]) < score_threshold:  # IP 相似度阈值
                continue
        except (TypeError, ValueError):
            continue
        entity = hit.get("entity") or {}
        meta = {}
        try:
            meta = json.loads(entity.get("metadata_json") or "{}")
        except (json.JSONDecodeError, AttributeError):
            pass
        hits.append(
            {
                "id": hit.get("id"),
                "doc_id": entity.get("doc_id"),
                "source": entity.get("source"),
                "chunk_index": entity.get("chunk_index"),
                "text": entity.get("text"),
                "metadata": meta,
                "score": round(float(hit["distance"]), 4),
            }
        )
    if len(hits) < top_k:
        logger.debug(
            "向量通道阈值过滤后不足 top_k: raw=%s kept=%s top_k=%s threshold=%s",
            len(results[0]) if results else 0, len(hits), top_k, score_threshold,
        )
    return hits[:top_k]


def stats() -> dict[str, Any]:
    """collection 统计信息，用于健康检查。"""
    try:
        s = _client().get_collection_stats(settings.milvus_collection)
        return {
            "collection": settings.milvus_collection,
            "num_entities": s.get("row_count", 0),
            "connected": True,
        }
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


# ───────────────────────── 图文双通道 ─────────────────────────
# 图片用独立 collection（agent_images）存多模态向量（维度=image_embedding_dim），
# 检索时与文本通道融合。全部默认关，开启由 settings.image_dual_channel 控制。
IMAGE_COLLECTION = "agent_images"


def _build_image_schema() -> CollectionSchema:
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=32),
        FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=500),
        FieldSchema(name="page", dtype=DataType.INT64),
        FieldSchema(name="image_index", dtype=DataType.INT64),
        FieldSchema(name="caption", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(name="metadata_json", dtype=DataType.VARCHAR, max_length=2000),
        FieldSchema(
            name="embedding",
            dtype=DataType.FLOAT_VECTOR,
            dim=settings.image_embedding_dim,
        ),
    ]
    return CollectionSchema(fields=fields, description="图文双通道图像向量", enable_dynamic_field=False)


def ensure_image_collection() -> str:
    """确保图片 collection 存在（幂等）。失败抛异常（上层降级为禁用图像通道）。"""
    client = _client()
    name = IMAGE_COLLECTION
    if not client.has_collection(name):
        client.create_collection(name, schema=_build_image_schema())
        _ensure_indexes(client, name)
        client.load_collection(name)
        logger.info("image collection '%s' 已创建", name)
    else:
        _ensure_indexes(client, name)
        try:
            client.load_collection(name)
        except Exception:
            pass
    return name


def add_image_vectors(records: list[dict[str, Any]]) -> list[str]:
    """写入图片向量（records: {user_id,source,page,image_index,caption,metadata,embedding}）。"""
    if not records:
        return []
    ensure_image_collection()
    rows = []
    for r in records:
        rows.append(
            {
                "id": uuid.uuid4().hex,
                "user_id": r["user_id"],
                "source": r["source"],
                "page": r.get("page", 0),
                "image_index": r.get("image_index", 0),
                "caption": (r.get("caption") or "")[:2000],
                "metadata_json": json.dumps(r.get("metadata") or {}, ensure_ascii=False),
                "embedding": r["embedding"],
            }
        )
    _client().insert(IMAGE_COLLECTION, rows)
    return [r["id"] for r in rows]


def delete_image_by_source(source: str, user_id: str | None = None) -> None:
    """按 source（可限定用户）删除该文档的所有图片向量（与 delete_by_source 配套）。"""
    escaped = source.replace("\\", "\\\\").replace('"', '\\"')
    expr = f'source == "{escaped}"'
    uf = user_filter_expr(user_id)
    if uf:
        expr += f" and {uf}"
    try:
        ensure_image_collection()
    except Exception:
        return
    _client().delete(IMAGE_COLLECTION, filter=expr)


def search_image(
    query_vec: list[float],
    top_k: int | None = None,
    user_id: str | None = "default",
) -> list[dict[str, Any]]:
    """图像通道检索：返回按相似度排序的图片候选（含 caption）。失败返回 []。"""
    top_k = top_k or settings.image_channel_top_k
    try:
        ensure_image_collection()
    except Exception:
        return []
    results = _client().search(
        IMAGE_COLLECTION,
        data=[query_vec],
        anns_field="embedding",
        search_params={"metric_type": settings.milvus_metric_type, "params": {"nprobe": 16}},
        limit=top_k * 3,
        output_fields=["source", "page", "image_index", "caption", "metadata_json"],
        filter=user_filter_expr(user_id) or "",
    )
    hits: list[dict[str, Any]] = []
    for hit in (results[0] if results else []):
        entity = hit.get("entity") or {}
        meta = {}
        try:
            meta = json.loads(entity.get("metadata_json") or "{}")
        except (json.JSONDecodeError, AttributeError):
            pass
        hits.append(
            {
                "source": entity.get("source"),
                "image_index": entity.get("image_index"),
                "page": entity.get("page"),
                "text": entity.get("caption") or "",
                "metadata": {"type": "image", **(meta or {})},
                "score": round(float(hit["distance"]), 4),
            }
        )
    return hits[:top_k]
