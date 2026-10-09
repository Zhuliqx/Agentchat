# -*- coding: utf-8 -*-
"""批量摄入：把"每文件一套流程"压成"整批一套流程"。

对比 ``ingest_file``（逐文件）：本模块对一批文件只做
**一次嵌入调用 + 少量分批 Postgres 提交 + 一次签名失效**。

Postgres 单事务行数过大会长时间无进展（实测 8.5 万块卡死 20 分钟以上），
所以新行按 ``PG_INSERT_BATCH`` 分批提交。

适用场景：首次批量导入语料（评估集、大语料）。
**不做增量对比**——已存在的 source 会被整体替换（语义等同 force_reingest）；
日常增量更新仍走 ``ingest_file``。

用法：
    from app.rag.batch_ingest import ingest_paths_batch
    stats = ingest_paths_batch(sorted(dir_path.glob("*.txt")), user_id="eval")
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import settings
from app.db.models import Document, gen_uuid, utcnow
from app.db.postgres import SessionLocal
from app.rag import vector_store
from app.rag.chunkers import split_text
from app.rag.extractors import load_document
from app.rag.hybrid import invalidate_docs_signature
from app.rag.ingestion import (
    _MARKDOWN_SUFFIX,
    _build_image_chunks,
    _build_table_chunks,
    _build_vlm_chunks,
    _write_image_vectors,
)

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm")

# 单批 PG 插入行数：单事务过大时 PG 长时间无进展（实测 8.5 万块卡死）
PG_INSERT_BATCH = 10_000


def discover_documents(directory: Path) -> list[Path]:
    """目录下所有支持的文档（后缀集合与 ingest_directory 保持一致）。"""
    return sorted(
        p
        for p in Path(directory).rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


@dataclass
class _FilePlan:
    """一个文件解析后的待写入计划。"""

    source: str
    filename: str
    chunks: list[dict[str, Any]] = field(default_factory=list)
    content_hash: str = ""
    images: list[Any] = field(default_factory=list)


def _plan_file(path: Path) -> _FilePlan | None:
    """解析 + 分块单个文件（不触碰任何库）。无内容的文件返回 None。"""
    source = str(Path(path).resolve())
    doc = load_document(Path(path))
    is_markdown = Path(path).suffix.lower() in _MARKDOWN_SUFFIX
    chunks = split_text(doc["text"], source, is_markdown=is_markdown)
    chunks += _build_table_chunks(doc["tables"], source)
    chunks += _build_image_chunks(doc["images"], source)
    chunks += _build_vlm_chunks(doc["images"], source)
    if not chunks:
        # 纯图片文档（OCR/VLM 都关）没有文本块，但图文双通道仍需要写图片向量
        if doc.get("images") and settings.image_dual_channel:
            return _FilePlan(
                source=source,
                filename=Path(path).name,
                chunks=[],
                images=doc["images"],
            )
        return None
    for idx, chunk in enumerate(chunks):
        if isinstance(chunk.get("metadata"), dict):
            chunk["metadata"]["chunk"] = idx
    content_hash = hashlib.sha256(
        "\n".join(c["text"] for c in chunks).encode("utf-8")
    ).hexdigest()
    return _FilePlan(
        source=source,
        filename=Path(path).name,
        chunks=chunks,
        content_hash=content_hash,
        images=doc.get("images") or [],
    )


def ingest_paths_batch(
    paths: Iterable[Path], user_id: str = "default", progress_cb=None
) -> dict[str, Any]:
    """批量摄入一组文件，返回 {files, chunks, failed}。

    失败语义与 ``ingest_file`` 一致：Postgres 先写 pending，Milvus 同步成功后才标
    synced；Milvus 失败的行保持 pending，交给对账任务补写。
    """

    def _progress(percent: int, stage: str) -> None:
        if progress_cb:
            progress_cb(percent, stage)

    # 1. 解析 + 分块（纯 CPU，失败的文件跳过并记录）
    _progress(5, "解析与分块")
    plans: list[_FilePlan] = []
    failed: list[dict[str, str]] = []
    for i, path in enumerate(paths):
        try:
            plan = _plan_file(Path(path))
        except Exception as exc:  # noqa: BLE001 - 单文件失败不影响整批
            logger.warning("批量摄入：解析失败 %s: %s", path, exc)
            failed.append({"filename": Path(path).name, "error": str(exc)})
            continue
        if plan is not None:
            plans.append(plan)
        if i % 50 == 0:
            _progress(5 + int(25 * i / max(1, len(list(paths)))), "解析与分块")

    if not plans:
        return {"files": 0, "chunks": 0, "failed": failed}

    # 2. 一次嵌入整批（内部分 batch，避免单次过大）
    _progress(35, "批量嵌入")
    texts: list[str] = [c["text"] for p in plans for c in p.chunks]
    if settings.embed_with_context:
        from app.rag.ingestion import _embed_context_text

        texts = [
            _embed_context_text(c, p.source)
            for p in plans
            for c in p.chunks
        ]
    from app.rag.embedding import embed_texts_cached

    vectors: list[list[float]] = []
    batch = max(1, int(settings.embed_batch_size or 32))
    for i in range(0, len(texts), batch):
        vectors.extend(embed_texts_cached(texts[i : i + batch]))
        _progress(35 + int(35 * min(1, (i + batch) / max(1, len(texts)))), "批量嵌入")

    if len(vectors) != len(texts):
        raise RuntimeError("嵌入返回数量与块数不一致")

    # 3. Postgres：整体替换各 source 的旧行（与首批插入同事务），其余行分批提交
    _progress(75, "写入关系库")
    sources = [p.source for p in plans]
    doc_ids: list[str] = [gen_uuid() for _ in texts]
    rows: list[Document] = []
    cursor = 0
    for plan in plans:
        for chunk_index, chunk in enumerate(plan.chunks):
            rows.append(
                Document(
                    id=doc_ids[cursor],
                    user_id=user_id,
                    filename=plan.filename,
                    source=plan.source,
                    chunk_index=chunk_index,
                    text=chunk["text"],
                    metadata_json=json.dumps(chunk.get("metadata") or {}, ensure_ascii=False),
                    content_hash=plan.content_hash,
                    vector_status="pending",
                )
            )
            cursor += 1
    with SessionLocal() as db:
        existing_sources = {
            s
            for (s,) in db.query(Document.source)
            .filter(Document.user_id == user_id, Document.source.in_(sources))
            .distinct()
            .all()
        }
        db.query(Document).filter(
            Document.user_id == user_id, Document.source.in_(sources)
        ).delete(synchronize_session=False)
        db.add_all(rows[:PG_INSERT_BATCH])
        db.commit()
    for start in range(PG_INSERT_BATCH, len(rows), PG_INSERT_BATCH):
        with SessionLocal() as db:
            db.add_all(rows[start : start + PG_INSERT_BATCH])
            db.commit()

    # 4. Milvus：每个 source 删旧（重导入要清），随后**整批一次 insert**
    _progress(85, "写入向量库")
    entries: list[dict[str, Any]] = []
    offset = 0
    for plan in plans:
        n = len(plan.chunks)
        ids = doc_ids[offset : offset + n]
        entries.append(
            {
                "source": plan.source,
                "chunks": plan.chunks,
                "doc_ids": ids,
                "vectors": vectors[offset : offset + n],
            }
        )
        offset += n

    # 删旧向量：只删"原本就有"的源（首次导入没有旧向量可删）；且**按组分批删**
    # ——delete_by_source 每次约 300ms（实测，与是否命中无关），逐源调用在千级
    # 文档下纯属 RPC 开销。
    synced_ids = doc_ids
    to_clean = [p.source for p in plans if p.source in existing_sources]
    if to_clean:
        try:
            vector_store.delete_by_sources(to_clean, user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - 留待对账任务补同步
            logger.warning("批量摄入：批量清理旧向量失败: %s", exc)
    if settings.image_dual_channel:
        for plan in plans:
            if plan.images:
                _write_image_vectors(plan.images, plan.source, user_id)

    try:
        vector_store.add_chunks_multi(entries, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - 整批插入失败则全部留 pending
        logger.warning("批量摄入：Milvus 批量写入失败（留待对账）: %s", exc)
        synced_ids = []

    # 5. 一次事务标记 synced
    if synced_ids:
        with SessionLocal() as db:
            db.query(Document).filter(Document.id.in_(synced_ids)).update(
                {
                    Document.vector_status: "synced",
                    Document.vector_synced_at: utcnow(),
                },
                synchronize_session=False,
            )
            db.commit()

    # 6. 签名失效只做一次
    invalidate_docs_signature(user_id)
    _progress(100, "完成")
    return {"files": len(plans), "chunks": len(texts), "failed": failed}
