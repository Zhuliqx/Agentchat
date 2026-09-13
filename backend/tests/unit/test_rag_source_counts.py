"""引用溯源注册表：按来源累加命中片段数，并保持首次命中顺序。"""
from __future__ import annotations

from app.agents.tools.sources import (
    _record_rag_sources,
    clear_rag_sources,
    get_recent_rag_source_refs,
)


def test_hits_accumulate_per_source_and_keep_first_seen_order():
    run_id = "test-run-hits"
    clear_rag_sources(run_id)

    # 第一次检索：company.md 命中 2 段，api.md 命中 1 段
    _record_rag_sources(run_id, [("company.md", 2), ("api.md", 1)])
    # 第二次检索：company.md 再命中 3 段
    _record_rag_sources(run_id, [("company.md", 3), ("policies.md", 1)])

    assert get_recent_rag_source_refs(run_id) == [
        {"path": "company.md", "hits": 5},
        {"path": "api.md", "hits": 1},
        {"path": "policies.md", "hits": 1},
    ]
    clear_rag_sources(run_id)


def test_empty_run_id_and_invalid_counts_are_ignored():
    _record_rag_sources("", [("a.md", 1)])
    assert get_recent_rag_source_refs("") == []

    run_id = "test-run-invalid"
    clear_rag_sources(run_id)
    _record_rag_sources(run_id, [("", 2), ("b.md", 0), ("c.md", 1)])
    assert get_recent_rag_source_refs(run_id) == [{"path": "c.md", "hits": 1}]
    clear_rag_sources(run_id)
