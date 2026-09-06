"""检索结果 identity 缓存集成测试（需 Postgres + Redis）。

验证：Redis 只存命中身份与分数，不存正文；缓存命中后用 Postgres 原文重建
Document，且不会再次执行检索管线。
"""
from __future__ import annotations

import uuid

import pytest
from langchain_core.documents import Document as LCDocument

from helpers import postgres_available, redis_available

pytestmark = pytest.mark.skipif(
    not (redis_available() and postgres_available()),
    reason="需要运行中的 Postgres + Redis",
)


def test_retrieval_cache_rebuilds_doc_text_from_db(monkeypatch):
    from app.config import settings
    from app.rag import hybrid
    from app.rag import retrieval_cache
    from app.rag.retriever import MilvusRetriever
    from app.cache.redis_client import get_redis
    from app.db.models import Document
    from app.db.postgres import SessionLocal

    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "retrieval_cache_enabled", True)
    monkeypatch.setattr(settings, "retrieval_cache_ttl_seconds", 60)
    monkeypatch.setattr(settings, "image_dual_channel", False)

    user_id = f"rc-user-{uuid.uuid4().hex[:8]}"
    source = f"/tmp/rc-{uuid.uuid4().hex[:8]}.txt"
    db_text = f"仅存在于 Postgres 的缓存重建正文 {uuid.uuid4().hex[:8]}"
    doc_id = uuid.uuid4().hex

    with SessionLocal() as db:
        db.add(
            Document(
                id=doc_id,
                user_id=user_id,
                filename="rc.txt",
                source=source,
                chunk_index=0,
                text=db_text,
                metadata_json="{}",
                vector_status="synced",
            )
        )
        db.commit()

    client = get_redis()
    assert client is not None
    signature = hybrid._docs_signature(user_id)
    key = retrieval_cache.build_key(user_id, "缓存查询", signature)
    client.delete(key)

    calls: list[str] = []

    def fake_retrieve(self, query: str):
        calls.append(query)
        return [
            LCDocument(
                page_content="这条临时正文不应进入 Redis",
                metadata={
                    "source": source,
                    "chunk_index": 0,
                    "score": 0.95,
                    "rerank_score": 0.1,
                },
            )
        ]

    monkeypatch.setattr(MilvusRetriever, "_retrieve_uncached", fake_retrieve)
    try:
        r = MilvusRetriever(user_id=user_id, top_k=4)
        first = r._get_relevant_documents("缓存查询")
        second = r._get_relevant_documents("缓存查询")

        assert calls == ["缓存查询"]
        assert first[0].page_content == "这条临时正文不应进入 Redis"
        assert second[0].page_content == db_text
        assert second[0].metadata["source"] == source

        raw = client.get(key)
        assert raw is not None
        payload = raw.decode("utf-8")
        assert db_text not in payload
        assert "这条临时正文不应进入 Redis" not in payload
    finally:
        client.delete(key)
        with SessionLocal() as db:
            doc = db.get(Document, doc_id)
            if doc:
                db.delete(doc)
                db.commit()
