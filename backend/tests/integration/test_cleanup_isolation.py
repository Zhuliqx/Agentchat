"""集成测试数据隔离：purge_test_user 必须同时清掉 PG 与 Milvus 残留。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helpers import db_available, milvus_user_count, purge_test_user, wait_milvus_visible

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

TEST_USER = "test-cleanup-isolation"

pytestmark = pytest.mark.skipif(
    not db_available(), reason="Postgres/Milvus 不可用（需 Docker 依赖）"
)


def test_purge_test_user_removes_pg_and_milvus_rows(tmp_path: Path) -> None:
    from app.config import settings
    from app.db.models import Document, gen_uuid
    from app.db.postgres import SessionLocal
    from app.rag import vector_store

    purge_test_user(TEST_USER)
    doc_id = gen_uuid()
    source = str((tmp_path / "isolation.txt").resolve())
    with SessionLocal() as db:
        db.add(
            Document(
                id=doc_id,
                user_id=TEST_USER,
                filename="isolation.txt",
                source=source,
                chunk_index=0,
                text="隔离测试内容",
                metadata_json="{}",
                vector_status="synced",
            )
        )
        db.commit()
    vector_store.add_chunks(
        [{"text": "隔离测试内容", "metadata": {}}],
        doc_ids=[doc_id],
        source=source,
        user_id=TEST_USER,
        vectors=[[0.1] * settings.embedding_dim],
    )
    wait_milvus_visible(source, min_rows=1)
    assert milvus_user_count(TEST_USER) >= 1

    purge_test_user(TEST_USER)

    assert milvus_user_count(TEST_USER) == 0
    with SessionLocal() as db:
        assert db.query(Document).filter(Document.user_id == TEST_USER).count() == 0
