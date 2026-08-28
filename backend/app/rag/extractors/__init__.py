"""文档解析（文本提取 + 表格/图片/按页抽取），按文件类型分模块。

- html.py：HTML → 纯文本（stdlib HTMLParser）
- docx.py：DOCX → 纯文本（段落 + 表格）
- pdf.py：PDF → 文本（pdfplumber→pymupdf→pypdf 逐级回退）
- text.py：入口分发（load_text / load_document）
"""
from __future__ import annotations

from app.rag.extractors.docx import _docx_to_text
from app.rag.extractors.html import _HtmlTextExtractor, _html_to_text
from app.rag.extractors.pdf import (
    _pdf_extract,
    _via_pdfplumber,
    _via_pymupdf,
    _via_pypdf,
)
from app.rag.extractors.text import _read_text_auto, load_document, load_text

__all__ = [
    "load_text",
    "load_document",
    "_read_text_auto",
    "_html_to_text",
    "_HtmlTextExtractor",
    "_docx_to_text",
    "_pdf_extract",
    "_via_pdfplumber",
    "_via_pymupdf",
    "_via_pypdf",
]
