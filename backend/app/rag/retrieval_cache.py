"""检索结果 identity 缓存。

Redis 只存“命中身份 + 分数 + 元数据”，不存知识库正文；命中后用 Postgres
原文重建 Document。这样缓存仍然是可丢失的短时状态，不放大正文泄露面。
"""
from __future__ import annotations

import hashlib
import json

from langchain_core.documents import Document as LCDocument
from sqlalchemy import select

from app.cache.redis_client import get_redis, redis_key
from app.config import settings
from app.db.models import Document
from app.db.postgres import SessionLocal

# 指纹不纳入连接密钥：密钥轮换不应让所有检索缓存失效
_SECRET_FIELDS = frozenset(
    {
        "auth_secret",
        "postgres_password",
        "redis_password",
        "openai_api_key",
        "deepseek_api_key",
        "dashscope_api_key",
        "tavily_api_key",
        "image_vlm_api_key",
        "langfuse_public_key",
        "langfuse_secret_key",
    }
)


def _config_fingerprint() -> str:
    """参与检索结果的全部配置哈希：改配置后旧缓存自然 miss。"""
    data = settings.model_dump(mode="json")
    for field in _SECRET_FIELDS:
        data.pop(field, None)
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_key(user_id: str, query: str, signature: tuple) -> str:
    """检索缓存 key：用户/query/配置/文档集签名全部参与，防止串用户与过期命中。"""
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    config_hash = _config_fingerprint()
    count, latest = signature
    sig = f"{count}|{latest.isoformat() if latest is not None else ''}"
    sig_hash = hashlib.sha256(sig.encode("utf-8")).hexdigest()
    return redis_key(
        "cache",
        "retr",
        "v1",
        user_hash,
        query_hash,
        config_hash[:16],
        sig_hash[:16],
    )


def _truncate_text(text: str) -> str:
    max_chars = settings.rag_max_chunk_chars
    return text if max_chars <= 0 else text[:max_chars]


def load_cached_docs(
    user_id: str,
    query: str,
    signature: tuple,
) -> list[LCDocument] | None:
    """读取并重建缓存结果；任意正文缺失/结构不合法时返回 None（视为 miss）。"""
    if not settings.retrieval_cache_enabled:
        return None
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(build_key(user_id, query, signature))
        if raw is None:
            return None
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None

    items = payload.get("docs") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None

    docs: list[LCDocument] = []
    for item in items:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if not isinstance(metadata, dict):
            return None
        source = metadata.get("source")
        chunk_index = metadata.get("chunk_index")
        if (
            not isinstance(source, str)
            or chunk_index is None
            or metadata.get("image_index") is not None
        ):
            return None  # 图文通道命中不缓存，遇到即整体 miss
        try:
            chunk_index = int(chunk_index)
        except (TypeError, ValueError):
            return None
        with SessionLocal() as db:
            text = db.scalar(
                select(Document.text).where(
                    Document.user_id == user_id,
                    Document.source == source,
                    Document.chunk_index == chunk_index,
                )
            )
        if text is None:
            return None
        docs.append(
            LCDocument(page_content=_truncate_text(text), metadata=metadata)
        )
    return docs


def store_cached_docs(
    user_id: str,
    query: str,
    signature: tuple,
    docs: list[LCDocument],
) -> None:
    """保存命中身份与分数；正文不落 Redis。不支持重建的命中整批不缓存。"""
    if not settings.retrieval_cache_enabled:
        return
    client = get_redis()
    if client is None:
        return

    records: list[dict] = []
    for doc in docs:
        metadata = dict(doc.metadata or {})
        source = metadata.get("source")
        chunk_index = metadata.get("chunk_index")
        if (
            not isinstance(source, str)
            or chunk_index is None
            or metadata.get("image_index") is not None
        ):
            return
        metadata.pop("text", None)
        try:
            json.dumps(metadata)
        except (TypeError, ValueError):
            return
        records.append({"metadata": metadata})

    payload = json.dumps({"v": 1, "docs": records}, ensure_ascii=False)
    try:
        client.set(
            build_key(user_id, query, signature),
            payload,
            ex=int(settings.retrieval_cache_ttl_seconds),
        )
    except Exception:  # noqa: BLE001
        pass  # 写失败只损失缓存收益
