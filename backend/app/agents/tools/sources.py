"""引用溯源注册表：按单次 Agent 执行的 run_id 记录检索来源。

不用“user_id 单槽”，避免同用户并发会话互相覆盖；也不用纯 contextvar
（子 Agent 可能在独立线程执行，contextvar 不透传）。每次 stream_agent
生成唯一 run_id，主流程结束时读取并清理该 key。
"""
from __future__ import annotations

import threading

_RAG_SOURCES: dict[str, list[str]] = {}
_RAG_SOURCES_LOCK = threading.Lock()


def _record_rag_sources(run_id: str, sources: list[str]) -> None:
    if not run_id:
        return
    with _RAG_SOURCES_LOCK:
        bucket = _RAG_SOURCES.setdefault(run_id, [])
        for src in sources:
            if src and src not in bucket:
                bucket.append(src)


def get_recent_rag_sources(run_id: str) -> list[str]:
    with _RAG_SOURCES_LOCK:
        return list(_RAG_SOURCES.get(run_id, []))


def clear_rag_sources(run_id: str) -> None:
    with _RAG_SOURCES_LOCK:
        _RAG_SOURCES.pop(run_id, None)
