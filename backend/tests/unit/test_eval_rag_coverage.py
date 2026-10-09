"""eval_rag 的"全命中"覆盖计算：多文档题必须集齐全部支撑文档。"""
from __future__ import annotations

from scripts.eval_rag import _coverage


def test_coverage_single_source_matches_first_rank():
    hits = [{"source": "/a/x.md"}, {"source": "/a/y.md"}]
    assert _coverage("source", ["/a/x.md"], hits) == (1, 1)


def test_coverage_requires_all_sources():
    hits = [{"source": "/a/y.md"}, {"source": "/a/x.md"}]
    assert _coverage("source", ["/a/x.md", "/a/y.md"], hits) == (1, 2)


def test_coverage_incomplete_when_one_source_missing():
    hits = [{"source": "/a/x.md"}]
    assert _coverage("source", ["/a/x.md", "/a/missing.md"], hits) == (1, None)


def test_coverage_without_expectation_is_none():
    assert _coverage("source", [], [{"source": "/a/x.md"}]) == (None, None)
