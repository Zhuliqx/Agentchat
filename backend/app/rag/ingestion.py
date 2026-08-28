"""文档摄入：加载 -> 分块 -> 嵌入 -> 写入 Milvus，并在 Postgres 记录元数据。

支持格式：txt / md / pdf / docx / html

职责边界（结构拆分后）：
- 文档解析（文本/表格/图片/按页抽取）→ ``app.rag.extractors``；
- 分块与增强块构建（Markdown 标题/PDF 按页/表格/OCR/VLM）→ ``app.rag.chunkers``；
- 本文件只做摄入流程编排：增量对比、嵌入、跨库写入与状态标记。

一致性模型（Postgres 为事实源、Milvus 为派生索引）：
- 写入：先写 Postgres（transactional，vector_status='pending'）→ 幂等同步 Milvus
  （``vector_store.sync_chunks``，按 doc_id 删+插）→ 标记 synced；
  PG 失败 → 无残留；Milvus 失败 → 行保持 pending，由 ``reconcile_vectors``
  对账任务补同步，不再依赖"补偿删除"。
- 删除：先删 Postgres 事实源，Milvus 尽力删除；残留幽灵向量由对账任务清理。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.models import Document, gen_uuid, utcnow
from app.db.postgres import SessionLocal
from app.rag import vector_store
from app.rag.chunkers import (
    _build_image_chunks,
    _build_table_chunks,
    _build_vlm_chunks,
    _chunk_hash,
    _split_pdf_pages,
    split_text,
)
from app.rag.extractors import load_document
from app.rag.hybrid import invalidate_docs_signature
from app.rag.section import section_prefix

logger = logging.getLogger(__name__)

_MARKDOWN_SUFFIX = {".md", ".markdown"}


def _embed_context_text(chunk: dict[str, Any], source: str) -> str:
    """嵌入侧上下文增强（EMBED_WITH_CONTEXT）：给块文本加章节/文件名前缀。

    仅影响**嵌入输入**，存储文本（Document.text / BM25 / 展示）不变——
    调用方用返回值做 embedding，仍把原 text 入库（见 ingest_file）。
    前缀提取见 app.rag.section.section_prefix。
    """
    text = str(chunk.get("text") or "")
    prefix = section_prefix(chunk.get("metadata") or {}, source)
    return f"{prefix}\n{text}" if prefix else text


def _write_image_vectors(images: list, source: str, user_id: str) -> int:
    """图文双通道：用多模态编码器对每张图编码 → 写 image_vectors collection。

    先删该 source 旧图片向量再写（幂等覆盖，避免 ghost）。编码 / 模型失败 → 降级为 0（不影响文本摄入）。
    """
    if not settings.image_dual_channel:
        return 0
    try:
        from app.rag.image_embedding import get_image_embedder

        embedder = get_image_embedder()
    except Exception:
        return 0
    try:
        vector_store.delete_image_by_source(source, user_id)
    except Exception:
        pass
    records = []
    for idx, img in enumerate(images or []):
        try:
            vec = embedder.encode_image(img)
        except Exception:
            continue
        cap = ""
        if settings.image_vlm_enabled:
            try:
                from app.rag import vlm

                cap = vlm.describe_image(img)
            except Exception:
                cap = ""
        records.append(
            {
                "user_id": user_id,
                "source": source,
                "page": 0,
                "image_index": idx,
                "caption": cap or f"[图片] {Path(source).name} 第{idx}张",
                "metadata": {"source": source, "image_index": idx, "type": "image"},
                "embedding": vec,
            }
        )
    if records:
        vector_store.add_image_vectors(records)
    return len(records)


def ingest_file(
    path: Path,
    filename: str | None = None,
    user_id: str = "default",
    progress_cb=None,
    force_reingest: bool = False,
) -> dict[str, Any]:
    """摄入单个文件到向量库（按用户隔离），返回统计信息。

    增量策略（内容指纹去重，仅作用于该用户的同名文档）：
    - 内容与已有块完全相同的块 → 跳过（不重复嵌入/写入）；
    - 新增/变化的块 → 嵌入并写入；
    - 旧文档中已消失的块 → 从 Postgres 与 Milvus 删除；
    - 整篇无变化 → 直接返回 unchanged=True（不触碰任何数据）。
    原子性：先解析成功再删旧增新，失败时旧数据不受影响。

    force_reingest：True 时跳过增量，先删除该 source 的旧块再全量重建。
    用于分块配置变化（chunk_size / strip_headers 等）的重新摄入——此时位置索引
    体系改变，增量会因 chunk_index 与保留块冲突而报 UniqueViolation。

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
    doc = load_document(path)
    text = doc["text"]
    _progress(15, "分块中")
    is_markdown = path.suffix.lower() in _MARKDOWN_SUFFIX
    chunks = split_text(text, source, is_markdown=is_markdown)
    # PDF 按页分块（PDF_PAGE_META，默认关）：每页独立分块并写 metadata["page"]
    if settings.pdf_page_meta and doc.get("pages"):
        chunks = _split_pdf_pages(doc["pages"], source)
    # 文档解析增强：表格结构化块 + 图片 OCR 块 + 图片 VLM 语义描述块（默认关时为空，行为不变）
    table_chunks = _build_table_chunks(doc["tables"], source)
    image_chunks = _build_image_chunks(doc["images"], source)
    vlm_chunks = _build_vlm_chunks(doc["images"], source)
    if table_chunks or image_chunks or vlm_chunks:
        _progress(22, "结构化解析")
    chunks = chunks + table_chunks + image_chunks + vlm_chunks
    if not chunks:
        # 无文本块时：仍需删旧图向量（force/换配置）或写新图向量（双通道开启）
        if force_reingest:
            vector_store.delete_image_by_source(source, user_id)
        if settings.image_dual_channel and doc["images"]:
            _write_image_vectors(doc["images"], source, user_id)
        _progress(100, "无内容可摄入")
        return {"filename": filename, "chunks": 0, "source": source}
    # 统一块序号，保证 doc_id:chunk_index 融合键唯一
    for idx, c in enumerate(chunks):
        if isinstance(c.get("metadata"), dict):
            c["metadata"]["chunk"] = idx
    new_hashes = [_chunk_hash(c["text"]) for c in chunks]
    _progress(25, "分块完成")
    # 图文双通道：图片向量写入独立 collection（默认关；放在增量判断前，保证文本 unchanged 也写）
    if settings.image_dual_channel and doc["images"]:
        _write_image_vectors(doc["images"], source, user_id)

    # 文档级内容去重：整篇内容已在库 → 跳过（跨文件同内容）
    content_hash = hashlib.sha256(
        "\n".join(c["text"] for c in chunks).encode("utf-8")
    ).hexdigest()
    if settings.doc_level_dedup:
        with SessionLocal() as db:
            dup = (
                db.query(Document.id)
                .filter(Document.user_id == user_id, Document.content_hash == content_hash)
                .first()
            )
            if dup:
                _progress(100, "内容已存在，跳过")
                return {
                    "filename": filename,
                    "chunks": len(chunks),
                    "source": source,
                    "unchanged": True,
                    "deduped": True,
                }

    if force_reingest:
        # 全量重建：先删 Postgres 事实源，再尽力删 Milvus（残留由对账清理），
        # 避免分块配置变化导致的 chunk_index 冲突
        _progress(30, "全量重建")
        with SessionLocal() as db:
            db.query(Document).filter(
                Document.source == source, Document.user_id == user_id
            ).delete(synchronize_session=False)
            db.commit()
        try:
            vector_store.delete_by_source(source, user_id)
            vector_store.delete_image_by_source(source, user_id)
        except Exception as exc:
            logger.warning("全量重建删除 Milvus 旧向量失败（对账任务将清理）: %s", exc)
        stale_ids = []
        to_write = [(i, c) for i, c in enumerate(chunks)]
    else:
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

    # 4. 删除已消失的块（先删 Postgres 事实源，再尽力删 Milvus；残留由对账清理）
    if stale_ids:
        _progress(35, "清理过期分块")
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(stale_ids)).delete(
                synchronize_session=False
            )
            db.commit()
        try:
            vector_store.delete_by_ids(stale_ids)
        except Exception as exc:
            logger.warning("删除 Milvus 过期向量失败（对账任务将清理）: %s", exc)

    # 5. 增量：分批嵌入 + 先写 Postgres（pending）再幂等同步 Milvus，最后标记 synced
    if to_write:
        from app.rag.embedding import get_embedder

        embedder = get_embedder()
        _progress(40, "生成向量嵌入")
        new_chunks = [c for _, c in to_write]
        texts = [c["text"] for c in new_chunks]
        # 上下文嵌入增强（默认关）：嵌入输入带章节/文件名前缀，存储文本不变
        if settings.embed_with_context:
            texts = [_embed_context_text(c, source) for c in new_chunks]
        batch = max(1, int(settings.embed_batch_size or 32))
        n = len(texts)
        vectors: list = []
        for i in range(0, n, batch):
            vectors.extend(embedder.embed_texts(texts[i : i + batch]))
            _progress(40 + int(40 * min(1, (i + batch) / max(1, n))),
                      f"嵌入 {min(i + batch, n)}/{n}")
        _progress(80, "写入向量库")
        # 先占位 doc_id，先写 Postgres（事务性事实源，pending），
        # 再幂等同步 Milvus（sync_chunks 按 doc_id 删+插）；Milvus 失败保持 pending，
        # 由 reconcile_vectors 对账任务补同步。
        doc_ids = [gen_uuid() for _ in new_chunks]
        with SessionLocal() as db:
            for idx, (orig_i, chunk) in enumerate(to_write):
                db.add(
                    Document(
                        id=doc_ids[idx],
                        user_id=user_id,
                        filename=filename,
                        source=source,
                        chunk_index=orig_i,
                        text=chunk["text"],
                        metadata_json=json.dumps(chunk["metadata"], ensure_ascii=False),
                        content_hash=content_hash,
                        vector_status="pending",
                    )
                )
            db.commit()
        vector_store.sync_chunks(
            new_chunks,
            doc_ids=doc_ids,
            source=source,
            user_id=user_id,
            vectors=vectors,
            chunk_indexes=[orig_i for orig_i, _ in to_write],
        )
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(doc_ids)).update(
                {
                    Document.vector_status: "synced",
                    Document.vector_synced_at: utcnow(),
                },
                synchronize_session=False,
            )
            db.commit()

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
