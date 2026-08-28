"""分块：基础分块 + Markdown 标题分块 + PDF 按页分块 + 表格/OCR/VLM 增强块。

纯逻辑模块（不触碰 Milvus/Postgres），供 ``ingestion.ingest_file`` 组装
文档块列表；块内容指纹（``_chunk_hash``）也归这里（增量去重的身份依据）。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings
from app.rag import image_parser, table_parser

_MD_HEADERS = [("#", "H1"), ("##", "H2"), ("###", "H3")]


def _base_splitter():
    """通用分块器：中文按段落/句号/感叹号/问号切分，兼顾语义完整性。

    separators 首位加入 `=====`：兼容用等号线分章的文本（如客服知识库），
    让章节独立成块、块内语义聚焦，避免"章节说明 + 正文"混块稀释 embedding。
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=[
            "=====",
            "\n\n",
            "\n",
            "。",
            "！",
            "？",
            ". ",
            "! ",
            "? ",
            " ",
            "",
        ],
    )


def split_text(
    text: str, source: str, is_markdown: bool = False
) -> list[dict[str, Any]]:
    """分块：Markdown 先按标题层级切分（保留标题上下文），再递归分块。

    纯长度分块会切断标题与正文的联系；按标题分块让每个块自带语义边界，
    检索时命中块能携带所在章节标题，显著提升中文文档召回质量。
    """
    if is_markdown and text.strip().startswith("#"):
        from langchain_text_splitters import MarkdownHeaderTextSplitter

        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_MD_HEADERS, strip_headers=settings.markdown_strip_headers
        )
        sections = md_splitter.split_text(text)
        base = _base_splitter()
        chunks: list[dict[str, Any]] = []
        for sec in sections:
            for piece in base.split_text(sec.page_content):
                if not piece.strip():
                    continue
                chunks.append(
                    {
                        "text": piece,
                        "metadata": {
                            **sec.metadata,
                            "source": source,
                            "chunk": len(chunks),
                        },
                    }
                )
        return chunks

    splitter = _base_splitter()
    return [
        {
            "text": chunk,
            "metadata": {"source": source, "chunk": i},
        }
        for i, chunk in enumerate(splitter.split_text(text))
    ]


def _split_pdf_pages(pages: list[str], source: str) -> list[dict]:
    """按页分块并写 ``metadata["page"]``（PDF_PAGE_META）。

    每页独立递归分块（页边界天然分隔章节），块数与纯文本拼接分块
    可能略有差异；需 reingest PDF 生效。
    """
    chunks: list[dict] = []
    splitter = _base_splitter()
    for page_no, page_text in enumerate(pages, 1):
        for piece in splitter.split_text(page_text or ""):
            if not piece.strip():
                continue
            chunks.append(
                {
                    "text": piece,
                    "metadata": {"source": source, "page": page_no, "chunk": len(chunks)},
                }
            )
    return chunks


def _build_table_chunks(tables: list[dict], source: str) -> list[dict]:
    """把每个表格按行列感知分块为结构化文本块（kind=table）。"""
    chunks: list[dict] = []
    mode = settings.table_to_text_mode
    max_rows = settings.table_max_rows_per_chunk
    for tbl in tables or []:
        for part in table_parser.split_table(tbl, max_rows):
            text = table_parser.table_to_text(part["headers"], part["rows"], mode)
            if not text.strip():
                continue
            chunks.append(
                {
                    "text": text,
                    "metadata": {
                        "source": source,
                        "kind": "table",
                        "columns": [str(c) for c in part["headers"]],
                    },
                }
            )
    return chunks


def _build_image_chunks(images: list, source: str) -> list[dict]:
    """对抽取的图片逐张 OCR，生成文本块（kind=ocr）；仅 OCR 开启时生效。OCR 失败/无字则跳过。"""
    if not settings.image_ocr_enabled:
        return []
    chunks: list[dict] = []
    for idx, img in enumerate(images or []):
        ocr = image_parser.ocr_text(img)
        if not ocr.strip():
            continue
        chunks.append(
            {
                "text": ocr,
                "metadata": {"source": source, "kind": "ocr", "image_index": idx},
            }
        )
    return chunks


def _build_vlm_chunks(images: list, source: str) -> list[dict]:
    """对图片逐张用 VLM 生成语义描述，生成文本块（kind=image_vlm）；失败/空则跳过。"""
    if not settings.image_vlm_enabled:
        return []
    from app.rag import vlm  # 延迟导入，避免加载时触发 openai 依赖

    chunks: list[dict] = []
    for idx, img in enumerate(images or []):
        caption = vlm.describe_image(img)
        if not caption.strip():
            continue
        chunks.append(
            {
                "text": f"[图片描述]\n{caption}",
                "metadata": {"source": source, "kind": "image_vlm", "image_index": idx},
            }
        )
    return chunks


def _chunk_hash(text: str) -> str:
    """文本块内容指纹（用于增量去重：内容未变的块不重复嵌入/写入）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
