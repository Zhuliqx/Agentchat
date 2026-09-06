"""检索结果缓存默认关闭：不触碰 Redis，每次仍执行完整检索。"""
from __future__ import annotations


def test_retrieval_cache_disabled_still_retrieves_each_time(monkeypatch):
    from app.config import settings
    from app.rag.retriever import MilvusRetriever

    monkeypatch.setattr(settings, "retrieval_cache_enabled", False)
    calls: list[str] = []

    def fake_retrieve(self, query: str):
        calls.append(query)
        return []

    monkeypatch.setattr(MilvusRetriever, "_retrieve_uncached", fake_retrieve)
    r = MilvusRetriever(user_id="cache-off-user")

    r._get_relevant_documents("问题一")
    r._get_relevant_documents("问题二")

    assert calls == ["问题一", "问题二"]
