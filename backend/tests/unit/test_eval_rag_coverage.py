"""eval_rag 的"全命中"覆盖计算：多文档题必须集齐全部支撑文档；图片期望同样计入。"""
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


def test_coverage_image_only_case():
    hits = [{"source": "/a/x.pdf", "image_index": 0}]
    assert _coverage("source", [], hits, ["/a/x.pdf#0"]) == (1, 1)


def test_coverage_image_only_miss():
    hits = [{"source": "/a/other.pdf", "image_index": 0}]
    assert _coverage("source", [], hits, ["/a/x.pdf#0"]) == (None, None)


def test_coverage_source_and_image_groups_are_or():
    # 图片组集齐即可（无需来源组）
    hits = [{"source": "/a/x.pdf", "image_index": 1}]
    assert _coverage("source", ["/a/missing.md"], hits, ["/a/x.pdf#1"]) == (1, 1)
    # 来源组集齐即可（无需图片组）
    hits2 = [{"source": "/a/doc.md"}, {"source": "/a/x.pdf", "image_index": 1}]
    assert _coverage("source", ["/a/doc.md"], hits2, ["/a/x.pdf#1"]) == (1, 1)
    # 两组都缺一半 → 未集齐
    hits3 = [{"source": "/a/x.pdf", "image_index": 2}]
    assert _coverage("source", ["/a/missing.md"], hits3, ["/a/x.pdf#1"]) == (None, None)
