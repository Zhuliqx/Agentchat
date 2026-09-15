"""引用溯源注册表：按单次 Agent 执行的 run_id 记录检索来源与命中片段数。

不用"user_id 单槽"，避免同用户并发会话互相覆盖；也不用纯 contextvar
（子 Agent 可能在独立线程执行，contextvar 不透传）。每次 stream_agent
生成唯一 run_id，主流程结束时读取并清理该 key。
"""
from __future__ import annotations

import threading
from collections.abc import Iterable

# run_id -> [(source, hits)]，按首次命中顺序排列，同一 run 内多次检索累加
_RAG_SOURCES: dict[str, list[tuple[str, int]]] = {}
_RAG_SOURCES_LOCK = threading.Lock()


def _record_rag_sources(run_id: str, counts: Iterable[tuple[str, int]]) -> dict[str, int]:
    """累加各来源命中片段数，返回 **run 级稳定编号**（来源 -> 1 开始的序号）。

    编号首次出现即分配、后续复用：一轮对话里检索工具可能被调用多次，若每次都从 1
    重新编号，模型引用的 [n] 会指向界面上另一个来源（来源列表按首次命中顺序排）。
    无 run_id（离线调用）时返回空表，调用方退回本次调用内的编号。
    """
    assigned: dict[str, int] = {}
    if not run_id:
        return assigned
    with _RAG_SOURCES_LOCK:
        bucket = _RAG_SOURCES.setdefault(run_id, [])
        index = {src: i for i, (src, _) in enumerate(bucket)}
        for src, hits in counts:
            if not src or hits <= 0:
                continue
            if src in index:
                i = index[src]
                bucket[i] = (src, bucket[i][1] + hits)
            else:
                bucket.append((src, hits))
                index[src] = len(bucket) - 1
            assigned[src] = index[src] + 1
    return assigned


def get_recent_rag_source_refs(run_id: str) -> list[dict]:
    """按首次命中顺序返回 [{"path": ..., "hits": 片段数}]。"""
    with _RAG_SOURCES_LOCK:
        return [{"path": src, "hits": hits} for src, hits in _RAG_SOURCES.get(run_id, [])]


def clear_rag_sources(run_id: str) -> None:
    with _RAG_SOURCES_LOCK:
        _RAG_SOURCES.pop(run_id, None)
