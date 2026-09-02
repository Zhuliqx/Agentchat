"""LangGraph 记忆存储（短期 Checkpointer + 长期 Store）。

- 短期记忆（运行时上下文持久化）：AsyncPostgresSaver，按 thread_id 持久化图状态
- 长期记忆（跨会话）：AsyncPostgresStore，namespace 隔离，跨线程持久

应用启动时 init_checkpointer() + init_store()，关闭时 close_*()。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import psycopg

from app.config import settings

logger = logging.getLogger(__name__)

_checkpointer = None
_conn = None

_store = None
_store_conn = None


async def init_checkpointer():
    """创建全局 Checkpointer 单例（幂等），失败返回 None（图降级为无状态）。"""
    global _checkpointer, _conn
    if _checkpointer is not None:
        return _checkpointer
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        _conn = await psycopg.AsyncConnection.connect(
            settings.postgres_conninfo, autocommit=True
        )
        _checkpointer = AsyncPostgresSaver(_conn)
        await _checkpointer.setup()  # 创建 checkpoint 表
        logger.info("Checkpointer 已就绪（Postgres）")
        return _checkpointer
    except Exception as exc:  # pragma: no cover
        logger.warning("Checkpointer 初始化失败，图将无状态运行: %s", exc)
        return None


def get_checkpointer():
    """同步获取全局 Checkpointer（可能为 None）。"""
    return _checkpointer


def _embed(texts: Sequence[str]) -> list[list[float]]:
    """Store 语义索引的 embedding 函数（复用 RAG 的 embedder）。

    注意：LangGraph 的 index.embed 期望签名是
    ``(texts: Sequence[str]) -> list[list[float]]``（接收序列返回向量列表），
    不能写成单个文本的 embed_query，否则 LangGraph 传入 list 时会把
    整个 list 再包一层导致 sentence-transformers 报
    "Unsupported input type: list"。
    """
    from app.rag.embedding import get_embedder

    return get_embedder().embed_texts(list(texts))


def _build_index():
    """构建 Store 语义索引（PostgresIndexConfig）。需要 Postgres 启用 pgvector。"""
    from langgraph.store.postgres.base import PostgresIndexConfig

    return PostgresIndexConfig(dims=settings.embedding_dim, embed=_embed, fields=None)


async def init_store():
    """创建全局长期记忆 Store 单例（幂等），失败返回 None。

    默认尝试启用语义索引（需 pgvector）；扩展缺失时自动降级为关键词检索。
    """
    global _store, _store_conn
    if _store is not None:
        return _store
    try:
        from langgraph.store.postgres import AsyncPostgresStore

        _store_conn = await psycopg.AsyncConnection.connect(
            settings.postgres_conninfo, autocommit=True
        )

        # 1) 尝试带语义索引初始化（需 pgvector 扩展）
        if settings.memory_semantic_search:
            try:
                _store = AsyncPostgresStore(_store_conn, index=_build_index())
                await _store.setup()
                logger.info("Store 已就绪（Postgres，语义检索已启用）")
                return _store
            except Exception as exc:  # 无 pgvector / 扩展未启用
                logger.warning(
                    "Store 语义索引初始化失败，降级为关键词检索: %s", exc
                )
                _store = None

        # 2) 降级：无索引 Store（仅关键词/全文检索）
        _store = AsyncPostgresStore(_store_conn)
        await _store.setup()  # 创建 store 表
        logger.info("Store 已就绪（Postgres，关键词检索模式）")
        return _store
    except Exception as exc:  # pragma: no cover
        logger.warning("Store 初始化失败，长期记忆不可用: %s", exc)
        return None


def get_store():
    """同步获取全局 Store（可能为 None）。"""
    return _store


def store_has_index() -> bool:
    """Store 是否启用了语义索引（决定 asearch 能否用 query 参数）。"""
    return bool(getattr(_store, "index_config", None))


async def safe_asearch(
    store: Any,
    namespace: tuple[str, ...],
    **kwargs: Any,
) -> list | None:
    """Store 检索的安全包装：失败返回 None，成功返回条目列表（可能为空）。

    统一各处「asearch 失败 → 降级」的 try/except 样板；调用方把 None 当作
    「检索不可用」处理（如降级全量扫描 / 跳过语义去重）。
    """
    try:
        return await store.asearch(namespace, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 检索失败不阻断主流程
        logger.warning("Store 检索失败（namespace=%s）: %s", namespace, exc)
        return None


def cleanup_stale_checkpoints(thread_ids: list[str] | None = None) -> int:
    """清理孤儿 checkpoint（thread_id 已不在 sessions 表中）。

    涉及 LangGraph Checkpointer 的三张表：checkpoints（状态快照）、
    checkpoint_blobs（去重数据块）、checkpoint_writes（增量写入日志）。

    - thread_ids 给定：定向清理这些线程的 checkpoint（删除会话后调用，避免全表扫）。
    - 否则：全量清理（启动时调用一次）。
    返回 0 表示成功、-1 表示失败（注意：非实际清理条数）。
    """
    try:
        from sqlalchemy import text as _text

        from app.db.postgres import engine

        with engine.begin() as conn:
            if thread_ids:
                for tid in thread_ids:
                    conn.execute(
                        _text("DELETE FROM checkpoint_writes WHERE thread_id = :tid"),
                        {"tid": tid},
                    )
                    conn.execute(
                        _text("DELETE FROM checkpoints WHERE thread_id = :tid"),
                        {"tid": tid},
                    )
                    conn.execute(
                        _text("DELETE FROM checkpoint_blobs WHERE thread_id = :tid"),
                        {"tid": tid},
                    )
            else:
                conn.execute(
                    _text(
                        "DELETE FROM checkpoint_writes "
                        "WHERE thread_id NOT IN (SELECT id FROM sessions)"
                    )
                )
                conn.execute(
                    _text(
                        "DELETE FROM checkpoints "
                        "WHERE thread_id NOT IN (SELECT id FROM sessions)"
                    )
                )
                conn.execute(
                    _text(
                        "DELETE FROM checkpoint_blobs "
                        "WHERE thread_id NOT IN (SELECT id FROM sessions)"
                    )
                )
        return 0
    except Exception as exc:  # pragma: no cover
        logger.warning("checkpoint 清理失败: %s", exc)
        return -1


async def close_checkpointer() -> None:
    global _checkpointer, _conn
    if _conn is not None:
        await _conn.close()
    _checkpointer = None
    _conn = None


async def close_store() -> None:
    global _store, _store_conn
    if _store_conn is not None:
        await _store_conn.close()
    _store = None
    _store_conn = None
