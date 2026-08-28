"""向量对账任务集成测试（Postgres 事实源 → Milvus 派生索引）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpers import db_available, wait_milvus_converged

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


pytestmark = pytest.mark.skipif(
    not db_available(), reason="Postgres/Milvus 不可用（需 Docker 依赖）"
)


def test_reconcile_syncs_pending_and_cleans_ghosts(tmp_path: Path) -> None:
    """对账任务：pending 行补同步、Milvus 幽灵 doc_id 被清理、状态标记正确。"""
    from app.config import settings
    from app.db.models import Document, gen_uuid
    from app.db.postgres import SessionLocal
    from app.rag import vector_store
    from app.rag.ingestion import ingest_file
    from app.scheduler import _run_reconcile_vectors

    doc = tmp_path / "kb_reconcile.txt"
    source = str(doc.resolve())
    try:
        # 1. 正常摄入 → 全部 synced
        doc.write_text("对账测试内容：量子对账引擎 v1，支持增量同步。", encoding="utf-8")
        r = ingest_file(doc, user_id="default")
        assert r.get("chunks", 0) > 0, f"摄入失败: {r}"
        with SessionLocal() as db:
            rows = db.query(Document).filter(Document.source == source).all()
        assert rows and all(x.vector_status == "synced" for x in rows)

        # 2. 人为制造 pending 行（PG 有、Milvus 无）
        pending_id = gen_uuid()
        with SessionLocal() as db:
            db.add(
                Document(
                    id=pending_id,
                    user_id="default",
                    filename="pending.txt",
                    source=source,
                    chunk_index=999,
                    text="仅存在于 PG 的 pending 块",
                    metadata_json="{}",
                    content_hash="pending-hash",
                    vector_status="pending",
                )
            )
            db.commit()

        # 3. 人为制造幽灵向量（Milvus 有、PG 无）
        ghost_id = gen_uuid()
        vector_store.add_chunks(
            [{"text": "幽灵向量", "metadata": {}}],
            doc_ids=[ghost_id],
            source=source,
            user_id="default",
            vectors=[[0.1] * settings.embedding_dim],
        )

        # 4. 跑对账
        stats = _run_reconcile_vectors()
        assert stats["pending_synced"] >= 1, stats
        assert stats["ghosts_deleted"] >= 1, stats

        # pending 行已标记 synced，且其向量已写入 Milvus
        with SessionLocal() as db:
            p = db.get(Document, pending_id)
        assert p is not None and p.vector_status == "synced"
        assert p.vector_synced_at is not None
        # Milvus 最终一致：轮询等待收敛到 Postgres 的 (doc_id, chunk_index) 集合
        with SessionLocal() as db:
            want = [
                (r.id, r.chunk_index)
                for r in db.query(Document).filter(Document.source == source).all()
            ]
        mv = wait_milvus_converged(source, want)
        mv_ids = {d for d, _ in mv}
        assert pending_id in mv_ids, "pending 行应被对账补写进 Milvus"
        assert ghost_id not in mv_ids, "幽灵 doc_id 应被对账清理"
    finally:
        vector_store.delete_by_source(source, user_id="default")
        with SessionLocal() as db:
            db.query(Document).filter(Document.source == source).delete(
                synchronize_session=False
            )
            db.commit()
