"""Langfuse 可观测性接入（fail-open）。

设计：
- 仅在 LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 三个变量
  都配置时才创建 handler；否则返回 None（后端照常运行，零侵入）。
- 接入方式：在 `run_agent`/`stream_agent` 的**每次 invocation** 把 handler
  放进 config["callbacks"]，由 LangGraph 传播给子 agent/LLM/tool——
  不写进 lru 缓存的图实例，避免跨会话复用 handler。
- 关闭时 flush，确保尾部 trace 不丢。
- 线程安全：初始化用 lock 保护；多 worker 并发首次调用只创建一次。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_handler: Optional[Any] = None


def langfuse_enabled() -> bool:
    return bool(
        getattr(settings, "langfuse_host", "")
        and getattr(settings, "langfuse_public_key", "")
        and getattr(settings, "langfuse_secret_key", "")
    )


def get_langfuse_handler() -> Optional[Any]:
    """返回 Langfuse CallbackHandler（未配置则 None）。线程安全、只初始化一次。"""
    global _handler
    if not langfuse_enabled():
        return None
    if _handler is not None:
        return _handler
    with _init_lock:
        if _handler is not None:  # 双检锁：并发首次调用只创建一次
            return _handler
        try:
            # langfuse 4.x：环境变量驱动。以 settings(.env) 为权威来源，
            # 强制覆盖进程环境变量，避免与已有环境变量冲突导致 host/key 不一致。
            import os

            os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
            os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
            os.environ["LANGFUSE_HOST"] = settings.langfuse_host

            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler

            Langfuse()  # 初始化全局客户端（读取 LANGFUSE_* 环境变量）
            _handler = CallbackHandler()
            logger.info("Langfuse 已启用（%s）", settings.langfuse_host)
            return _handler
        except Exception as exc:  # noqa: BLE001 - 观测失败不影响主流程
            logger.warning("Langfuse 初始化失败（自动禁用）: %s", exc)
            return None


def flush_langfuse() -> None:
    """关闭时 flush，避免尾部 trace 丢失。"""
    if _handler is not None:
        try:
            _handler.flush()
        except Exception:  # noqa: BLE001
            pass


def record_retrieval_stats(name: str, stats: dict, elapsed_ms: float) -> None:
    """记录检索链路内部指标（fail-open，调用方无需 try/except）。

    - 始终输出结构化日志（``RAG_METRIC`` 前缀），供离线分析检索各通道表现
      （通道命中数 / 分数分布 / 耗时），无需任何外部依赖；
    - langfuse 启用且当前上下文存在活动 span 时，额外以 span 上报
      （v4 ``start_as_current_observation``）。检索常在 ``asyncio.to_thread``
      线程内执行，contextvars 不传播 → 无活动 span 时自动跳过，不产生噪音
      或孤儿观测。
    """
    try:
        logger.info("RAG_METRIC %s elapsed_ms=%.1f stats=%s", name, elapsed_ms, stats)
        if not langfuse_enabled():
            return
        from langfuse import Langfuse

        lf = Langfuse()
        if lf.get_current_observation_id() is None:
            return
        with lf.start_as_current_observation(
            name=f"rag.{name}", type="SPAN", input=stats
        ):
            pass  # 耗时与入参已记录；上下文管理器退出时自动 end
    except Exception:  # noqa: BLE001 - 观测失败不影响检索
        pass
