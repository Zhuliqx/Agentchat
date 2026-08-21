"""文档摄入：加载 -> 分块 -> 嵌入 -> 写入 Milvus，并在 Postgres 记录元数据。

支持格式：txt / md / pdf / docx / html
"""
from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.models import Document
from app.db.postgres import SessionLocal
from app.rag import vector_store
from app.rag.hybrid import invalidate_docs_signature

_MARKDOWN_SUFFIX = {".md", ".markdown"}
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


class _HtmlTextExtractor(HTMLParser):
    """用 stdlib html.parser 提取 HTML 可见文本，丢弃 script/style 内容。

    相比直接 read_text，避免把 <script>/<style> 里的脚本/样式文本混入知识库。
    """

    _SKIP_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(raw: str) -> str:
    """HTML → 纯文本（剥离标签与脚本/样式）；解析失败时回退原始文本。"""
    parser = _HtmlTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return raw
    return parser.text() or raw


def _docx_to_text(path: Path) -> str:
    """DOCX → 纯文本：段落 + 表格（每行单元格用 | 连接）。

    相比只取 paragraphs，能把表格内容也纳入知识库，不丢信息。
    """
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def load_text(path: Path) -> str:
    """按扩展名读取文档为纯文本。"""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        return _read_text_auto(path)
    if suffix in (".html", ".htm"):
        return _html_to_text(_read_text_auto(path))
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix in (".docx",):
        return _docx_to_text(path)
    raise ValueError(f"不支持的文件类型: {suffix}")


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
            headers_to_split_on=_MD_HEADERS, strip_headers=False
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


def _chunk_hash(text: str) -> str:
    """文本块内容指纹（用于增量去重：内容未变的块不重复嵌入/写入）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_file(
    path: Path,
    filename: str | None = None,
    user_id: str = "default",
    progress_cb=None,
) -> dict[str, Any]:
    """摄入单个文件到向量库（按用户隔离），返回统计信息。

    增量策略（内容指纹去重，仅作用于该用户的同名文档）：
    - 内容与已有块完全相同的块 → 跳过（不重复嵌入/写入）；
    - 新增/变化的块 → 嵌入并写入；
    - 旧文档中已消失的块 → 从 Postgres 与 Milvus 删除；
    - 整篇无变化 → 直接返回 unchanged=True（不触碰任何数据）。
    原子性：先解析成功再删旧增新，失败时旧数据不受影响。

    progress_cb(percent: int, stage: str)：摄入阶段回调（供前端展示进度）。
    """

    def _progress(percent: int, stage: str) -> None:
        if progress_cb:
            progress_cb(percent, stage)

    path = Path(path)
    source = str(path.resolve())
    filename = filename or path.name

    # 1. 解析 + 分块（失败则不触碰旧数据）
    _progress(5, "读取文件")
    text = load_text(path)
    _progress(15, "分块中")
    is_markdown = path.suffix.lower() in _MARKDOWN_SUFFIX
    chunks = split_text(text, source, is_markdown=is_markdown)
    if not chunks:
        _progress(100, "无内容可摄入")
        return {"filename": filename, "chunks": 0, "source": source}
    new_hashes = [_chunk_hash(c["text"]) for c in chunks]
    _progress(25, "分块完成")

    # 2. 读取该用户已有同 source 块：hash -> id，用于增量对比
    existing: dict[str, str] = {}  # hash -> doc_id
    with SessionLocal() as db:
        rows = (
            db.query(Document.id, Document.text)
            .filter(Document.source == source, Document.user_id == user_id)
            .all()
        )
        for rid, rtext in rows:
            existing.setdefault(_chunk_hash(rtext), rid)

    stale_ids = [v for k, v in existing.items() if k not in set(new_hashes)]
    kept_hashes = set(existing.keys())
    to_write = [(i, c) for i, c in enumerate(chunks) if new_hashes[i] not in kept_hashes]

    # 3. 整篇无变化：直接返回，不触碰任何数据
    if not stale_ids and not to_write:
        _progress(100, "内容无变化，跳过")
        return {"filename": filename, "chunks": len(chunks), "source": source, "unchanged": True}

    # 4. 删除已消失的块（Postgres + Milvus，仅该用户）
    if stale_ids:
        _progress(35, "清理过期分块")
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(stale_ids)).delete(
                synchronize_session=False
            )
            db.commit()
        vector_store.delete_by_ids(stale_ids)

    # 5. 增量：仅嵌入 + 写入新增/变化块（chunk_index 用原始块索引）
    if to_write:
        from app.rag.embedding import get_embedder

        _progress(40, "生成向量嵌入")
        new_chunks = [c for _, c in to_write]
        vectors = get_embedder().embed_texts([c["text"] for c in new_chunks])
        _progress(80, "写入向量库")
        with SessionLocal() as db:
            docs = []
            for orig_i, (_, chunk) in enumerate(to_write):
                doc = Document(
                    user_id=user_id,
                    filename=filename,
                    source=source,
                    chunk_index=orig_i,
                    text=chunk["text"],
                    metadata_json=json.dumps(chunk["metadata"], ensure_ascii=False),
                )
                db.add(doc)
                docs.append(doc)
            db.commit()
            doc_ids = [d.id for d in docs]
        vector_store.add_chunks(
            new_chunks,
            doc_ids=doc_ids,
            source=source,
            user_id=user_id,
            vectors=vectors,
        )

    # 6. 失效文档集签名缓存（使 BM25 关键词通道立即包含新文档）
    invalidate_docs_signature()
    _progress(100, "完成")

    return {
        "filename": filename,
        "chunks": len(to_write),
        "source": source,
        "user_id": user_id,
        "unchanged": False,
    }


def ingest_directory(
    directory: Path, pattern: str = "*.{txt,md,pdf,docx,html}", user_id: str = "default"
) -> list[dict[str, Any]]:
    """批量摄入目录下所有支持的文件（归属指定用户的知识库）。"""
    results = []
    for file in Path(directory).rglob("*"):
        if file.suffix.lower() in (".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"):
            try:
                results.append(ingest_file(file, user_id=user_id))
            except Exception as exc:
                results.append({"filename": file.name, "error": str(exc)})
    return results
