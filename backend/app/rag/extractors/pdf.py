"""PDF → 纯文本：pdfplumber→pymupdf→pypdf 逐级回退。"""
from __future__ import annotations


def _via_pdfplumber(path) -> tuple[str, list[str]]:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        pages = [(page.extract_text() or "") for page in pdf.pages]
    return "\n".join(pages), pages


def _via_pymupdf(path) -> tuple[str, list[str]]:
    import pymupdf  # PyMuPDF 新名（fitz 已弃用）

    with pymupdf.open(str(path)) as doc:
        pages = [page.get_text("text") for page in doc]
    return "\n".join(pages), pages


def _via_pypdf(path) -> tuple[str, list[str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages), pages


def _pdf_extract(path) -> tuple[str, list[str]]:
    """PDF → (拼接文本, 按页文本)：pdfplumber→pymupdf→pypdf 逐级回退。

    相较 pypdf，pdfplumber/pymupdf 对中文/多栏/表格保留更好、控制字符更少。
    任一级失败/空则继续下一级，全部失败返回 ("", [])。
    """
    for fn in (_via_pdfplumber, _via_pymupdf, _via_pypdf):
        try:
            text, pages = fn(path)
            if text and text.strip():
                return text, pages
        except Exception:
            continue
    return "", []
