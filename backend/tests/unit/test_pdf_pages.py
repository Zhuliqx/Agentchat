"""PDF 按页分块单元测试。"""
from __future__ import annotations

from app.rag.chunkers import _split_pdf_pages


def test_split_pdf_pages_writes_page_metadata():
    pages = ["第一页内容。", "第二页内容。", "第三页内容。"]
    chunks = _split_pdf_pages(pages, "/tmp/kb/手册.pdf")
    assert len(chunks) >= 3
    pages_seen = {c["metadata"]["page"] for c in chunks}
    assert pages_seen == {1, 2, 3}


def test_split_pdf_pages_empty_page_skipped():
    chunks = _split_pdf_pages(["", "有内容。"], "/tmp/kb/a.pdf")
    assert chunks
    assert all(c["metadata"]["page"] == 2 for c in chunks)


def test_split_pdf_pages_long_page_split_into_multiple():
    page = "段落一。" * 300  # 超过 chunk_size=800 → 拆成多块
    chunks = _split_pdf_pages([page], "/tmp/kb/a.pdf")
    assert len(chunks) > 1
    assert all(c["metadata"]["page"] == 1 for c in chunks)


def test_split_pdf_pages_chunk_index_progresses():
    pages = ["第一页。", "第二页。"]
    chunks = _split_pdf_pages(pages, "/tmp/kb/a.pdf")
    indexes = [c["metadata"]["chunk"] for c in chunks]
    assert indexes == list(range(len(chunks)))
