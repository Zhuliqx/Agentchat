"""表格解析单元测试（纯逻辑，不依赖 pdf/docx/html 解析库）。"""
from __future__ import annotations

from app.rag.table_parser import (
    _HtmlTableParser,
    _make_table,
    split_table,
    table_to_markdown,
    table_to_nl,
    table_to_text,
)


def test_table_to_markdown():
    md = table_to_markdown(["名称", "数量"], [["A", "3"], ["B", "5"]])
    assert "| 名称 | 数量 |" in md
    assert "| A | 3 |" in md


def test_table_to_nl():
    nl = table_to_nl(["名称", "数量"], [["A", "3"], ["B", "5"]])
    assert "名称" in nl and "数量" in nl
    assert "名称=A" in nl and "数量=3" in nl


def test_table_to_text_modes():
    assert table_to_text(["a"], [["1"]], "markdown").startswith("|")
    assert "a=1" in table_to_text(["a"], [["1"]], "nl")


def test_make_table_header_detection():
    t = _make_table([["名称", "数量"], ["A", "3"], ["B", "5"]])
    assert t["headers"] == ["名称", "数量"]
    assert t["rows"] == [["A", "3"], ["B", "5"]]


def test_make_table_no_header_uses_placeholder():
    # 只有一行 → 无法当表头：用占位列名，全部作数据行
    t = _make_table([["A", "3"]])
    assert t["headers"] == ["列1", "列2"]
    assert t["rows"] == [["A", "3"]]


def test_split_table_by_rows():
    table = {"headers": ["名称", "数量"], "rows": [["A", "3"], ["B", "5"], ["C", "7"], ["D", "9"]]}
    parts = split_table(table, 3)
    assert len(parts) == 2
    assert parts[0]["headers"] == ["名称", "数量"]
    assert parts[0]["rows"] == [["A", "3"], ["B", "5"], ["C", "7"]]
    assert parts[1]["rows"] == [["D", "9"]]


def test_split_table_empty_rows():
    assert split_table({"headers": ["a"], "rows": []}, 5) == [{"headers": ["a"], "rows": []}]


def test_html_parser_tables():
    html = "<table><tr><th>名称</th><th>数量</th></tr><tr><td>A</td><td>3</td></tr></table>"
    parser = _HtmlTableParser()
    parser.feed(html)
    parser.close()
    assert parser.tables == [[["名称", "数量"], ["A", "3"]]]
