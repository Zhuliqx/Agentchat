"""文档分块单元测试（含 Markdown 标题分块）。"""
from __future__ import annotations

from app.rag.ingestion import split_text


def test_markdown_split_keeps_heading_metadata():
    md = (
        "# 产品介绍\n"
        "这是一段产品简介内容。\n"
        "## 技术特点\n"
        "特点一：高性能。特点二：易用。\n"
    )
    chunks = split_text(md, "test.md", is_markdown=True)
    assert chunks, "Markdown 应有分块结果"
    # 至少存在带 H1 元数据的块
    metas = [c["metadata"] for c in chunks]
    assert any(m.get("H1") == "产品介绍" for m in metas)


def test_plain_text_split_limits_chunk_size():
    text = "春天来了。" * 500  # 远超 chunk_size 的长文本
    chunks = split_text(text, "test.txt", is_markdown=False)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 800 + 1 for c in chunks)  # chunk_size=800


def test_markdown_without_heading_falls_back():
    """无标题的 md 内容应退回普通分块。"""
    text = "只有正文。" * 200
    chunks = split_text(text, "plain.md", is_markdown=True)
    assert len(chunks) >= 1


def test_empty_text_returns_empty():
    assert split_text("", "empty.txt", is_markdown=False) == []
