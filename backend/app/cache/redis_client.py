"""可选 Redis 基础设施封装。

设计边界：
- Redis 不作为事实源，只承载可丢失的短时状态；
- 默认关闭（``REDIS_ENABLED=false``）时应用不依赖 Redis 也能完整运行；
- 启用但连接失败：启动不阻断、健康检查报 degraded，由具体调用方决定降级策略。

当前已接入：登录失败计数（`app/api/routes/auth.py`）、摄入任务进度
（`app/api/routes/rag.py`）、BM25 文档集签名（`app/rag/hybrid.py`）、
embedding 向量缓存（`app/rag/embedding.py`，仅存向量不存原文）、
检索结果 identity 缓存（`app/rag/retrieval_cache.py`，正文仍从 Postgres 重建）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None  # Any：redis 包按需 import，未启用时不要求在模块加载期可用
_lock = threading.Lock()    # 防止多线程同时创建/关闭客户端


def _new_client() -> Any:
    """按配置创建惰性 Redis 客户端（连接真正发生时才会建立）。"""
    import redis

    common = {
        "socket_timeout": settings.redis_socket_timeout,
        "socket_connect_timeout": settings.redis_socket_timeout,
    }
    if settings.redis_url.strip():
        return redis.Redis.from_url(settings.redis_url.strip(), **common)
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password or None,  # 空密码按无认证处理，避免 redis-py 发送空 AUTH
        **common,
    )


def get_redis() -> Any | None:
    """返回惰性 Redis 客户端；未启用时返回 None。"""
    global _client

    if not settings.redis_enabled:
        return None
    with _lock:
        if _client is None:
            _client = _new_client()
        return _client


def close_redis() -> None:
    """关闭当前客户端并清空全局引用（lifespan 收尾 / 连接失败后重试）。"""
    global _client

    with _lock:
        client = _client
        _client = None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("关闭 Redis 连接失败（忽略）", exc_info=True)


def init_redis() -> None:
    """启动时做一次 PING；失败仅告警并关闭，不阻断应用启动。"""
    if not settings.redis_enabled:
        return
    try:
        client = get_redis()
        client.ping()
        endpoint = (
            settings.redis_url.strip()
            or f"{settings.redis_host}:{settings.redis_port}"
        )
        logger.info(
            "Redis 已连接 %s（db=%s）",
            endpoint,
            settings.redis_db,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连接失败（功能将按降级策略运行）: %s", exc)
        close_redis()


def redis_health() -> dict:
    """健康状态：未启用返回 disabled；启用后返回 ping 结果（永不抛异常）。"""
    if not settings.redis_enabled:
        return {"enabled": False}
    try:
        client = get_redis()
        client.ping()
        return {"enabled": True, "ok": True}
    except Exception as exc:  # noqa: BLE001
        close_redis()
        logger.warning("Redis ping 失败: %s", exc)
        return {"enabled": True, "ok": False, "error": str(exc)}


def redis_key(*parts: str) -> str:
    """生成统一前缀的 key：``{prefix}:{domain}:{object}``。"""
    prefix = (settings.redis_key_prefix or "agentchat").strip(":")
    return ":".join((prefix, *parts))
