"""引用溯源：最近一次知识库检索命中的来源（按 user_id 单槽）。"""
from __future__ import annotations

import threading

# 检索工具执行后写入，Supervisor 发 tool 事件时读取附加到 data.sources。
_RAG_SOURCES: dict[str, list[str]] = {}
_RAG_SOURCES_LOCK = threading.Lock()


def _record_rag_sources(user_id: str, sources: list[str]) -> None:
    with _RAG_SOURCES_LOCK:
        _RAG_SOURCES[user_id] = list(sources)


def get_recent_rag_sources(user_id: str) -> list[str]:
    with _RAG_SOURCES_LOCK:
        return list(_RAG_SOURCES.get(user_id, []))
