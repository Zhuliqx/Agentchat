"""规模池构建单测：分片读取顺序、探针过滤与截断。"""
from __future__ import annotations

from scripts.build_scale_pool import iter_pool_lines, load_probes, select_lines


def test_iter_pool_lines_sorted_and_normalized(tmp_path):
    (tmp_path / "documents_dup_part_1_part_2").write_text("b行\n\n", encoding="utf-8")
    (tmp_path / "documents_dup_part_1_part_1").write_text("a\n行 子\n", encoding="utf-8")
    assert list(iter_pool_lines(tmp_path)) == ["a", "行 子", "b行"]


def test_select_lines_filters_probes_and_limit():
    lines = ["目标新闻：某地发生某事", "无关新闻一", "无关新闻二"]
    assert select_lines(lines, ["某地发生某事"], None) == ["无关新闻一", "无关新闻二"]
    assert select_lines(lines, [], 2) == ["目标新闻：某地发生某事", "无关新闻一"]


def test_load_probes(tmp_path):
    (tmp_path / "x.txt").write_text("探 针 文 本", encoding="utf-8")
    assert load_probes([tmp_path]) == ["探 针 文 本"]
