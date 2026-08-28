"""HTML 文本提取单元测试（A1：内联拼接、块级换行、表格 | 分隔、列表 - 前缀）。"""
from __future__ import annotations

from app.rag.extractors.html import _html_to_text


def test_inline_tags_do_not_break_lines():
    # 内联 <b> 不应把 "Hello world!" 拆成两行
    assert _html_to_text("<p>Hello <b>world</b>!</p>") == "Hello world!"


def test_list_items_use_dash_prefix():
    html = "<ul><li>A</li><li>B</li></ul>"
    assert _html_to_text(html) == "- A\n- B"


def test_table_cells_joined_by_pipe():
    html = "<table><tr><th>名称</th><th>数量</th></tr><tr><td>A</td><td>3</td></tr></table>"
    assert _html_to_text(html) == "名称 | 数量\nA | 3"


def test_script_style_skipped():
    assert _html_to_text("<script>alert(1)</script><style>x{}</style>ok") == "ok"
