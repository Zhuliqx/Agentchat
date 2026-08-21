"""RRF 融合算法单元测试。"""
from __future__ import annotations

from app.rag.hybrid import _rrf


def test_rrf_boosts_items_in_both_lists():
    # doc_a 只在第一路排第 1，doc_b 只在第二路排第 1，doc_c 在两路都出现
    lists = [["a", "c"], ["c", "b"]]
    result = dict(_rrf(lists, k=60))
    # 交集项 c 得两路分，应排最前
    assert max(result, key=result.get) == "c"
    assert result["c"] > result["a"]
    assert result["c"] > result["b"]


def test_rrf_returns_sorted_desc():
    lists = [["x", "y", "z"], ["z"]]
    result = _rrf(lists, k=60)
    scores = [s for _, s in result]
    assert scores == sorted(scores, reverse=True)


def test_rrf_empty_lists():
    assert _rrf([], k=60) == []
