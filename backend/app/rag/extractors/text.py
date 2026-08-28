"""文本类文件读取入口（编码自动检测 + 按扩展名分发）。"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.rag import image_parser, table_parser
from app.rag.extractors.docx import _docx_to_text
from app.rag.extractors.html import _html_to_text
from app.rag.extractors.pdf import _pdf_extract


def _read_text_auto(path: Path) -> str:
    """读取文本类文件，自动检测编码（chardet），检测失败回退 utf-8 容错。

    相比固定 utf-8，能正确解析 GBK 等编码的中文文档，避免乱码/丢字。
    """
    raw = path.read_bytes()
    encoding: str = "utf-8"
    try:
        import chardet

        detected = chardet.detect(raw)
        if detected:
            enc = detected.get("encoding")
            if isinstance(enc, str) and enc:
                encoding = enc
    except Exception:
        pass
    try:
        return raw.decode(encoding, errors="ignore")
    except Exception:
        return raw.decode("utf-8", errors="ignore")


def load_text(path: Path) -> str:
    """按扩展名读取文档为纯文本。"""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        return _read_text_auto(path)
    if suffix in (".html", ".htm"):
        return _html_to_text(_read_text_auto(path))
    if suffix == ".pdf":
        return _pdf_extract(path)[0]
    if suffix in (".docx",):
        return _docx_to_text(path)
    raise ValueError(f"不支持的文件类型: {suffix}")


def load_document(path: Path) -> dict:
    """读取文档：返回 {"text", "tables", "images", "pages"}。

    表格与图片按开关可选解析（默认关=仅文本，行为不变）。图片 = PIL.Image 列表。
    pages：PDF_PAGE_META 开启时对 PDF 按页提取文本（默认关=空列表）。
    解析失败安全降级：返回空列表，不影响文本通道。
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, pages = _pdf_extract(path)
        if not settings.pdf_page_meta:
            pages = []
    else:
        text, pages = load_text(path), []
    tables: list[dict] = []
    if settings.table_extract and suffix in (".pdf", ".docx", ".html", ".htm"):
        tables = table_parser.parse_tables(path, suffix)
    images: list = []
    if (settings.image_ocr_enabled or settings.image_vlm_enabled or settings.image_dual_channel) and suffix == ".pdf":
        images = image_parser.extract_pdf_images(path)
    return {"text": text, "tables": tables, "images": images, "pages": pages}
