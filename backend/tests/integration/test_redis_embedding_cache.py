"""embedding 缓存 Redis 集成测试（需运行中的 Redis）。

验证缓存层把向量写入 Redis，且本地缓存清空后再次查询不会重复调用模型。
模型本身用可计数 fake 替代，只测缓存边界。
"""
from __future__ import annotations

import uuid

import pytest

from helpers import redis_available

pytestmark = pytest.mark.skipif(
    not redis_available(), reason="需要运行中的 Redis"
)


class _FakeEmbedder:
    def __init__(self) -> None:
        self.query_calls = 0
        self.batch_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [float(len(text)), 0.5]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [[float(len(t)), 0.25] for t in texts]


def _unique() -> str:
    return f"embed-cache-{uuid.uuid4().hex[:8]}"


def test_embed_query_cache_hits_redis_after_local_clear(monkeypatch):
    """query 向量写入 Redis；清掉进程内缓存后仍能命中，不重复调用模型。"""
    from app.config import settings
    from app.rag import embedding as embedding_mod
    from app.cache.redis_client import get_redis

    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    monkeypatch.setattr(settings, "embedding_cache_ttl_seconds", 600)
    monkeypatch.setattr(settings, "redis_enabled", True)
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_mod, "get_embedder", lambda: fake)
    client = get_redis()
    assert client is not None
    text = _unique()
    key = embedding_mod._embedding_cache_key(text)
    client.delete(key)
    embedding_mod.embed_query_cached.cache_clear()
    try:
        first = embedding_mod.embed_query_cached(text)
        embedding_mod.embed_query_cached.cache_clear()
        second = embedding_mod.embed_query_cached(text)

        assert fake.query_calls == 1
        assert first == second
        assert client.get(key) is not None
        ttl = client.ttl(key)
        assert 0 < ttl <= 600
    finally:
        embedding_mod.embed_query_cached.cache_clear()
        client.delete(key)


def test_embed_texts_cache_hits_redis(monkeypatch):
    """批量文本向量写入 Redis；第二次相同批次不再调用 embedder。"""
    from app.config import settings
    from app.rag import embedding as embedding_mod
    from app.cache.redis_client import get_redis

    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    monkeypatch.setattr(settings, "embedding_cache_ttl_seconds", 600)
    monkeypatch.setattr(settings, "redis_enabled", True)
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_mod, "get_embedder", lambda: fake)
    client = get_redis()
    assert client is not None
    texts = [_unique(), _unique() + "-b"]
    keys = [embedding_mod._embedding_cache_key(t) for t in texts]
    for key in keys:
        client.delete(key)
    try:
        first = embedding_mod.embed_texts_cached(texts)
        second = embedding_mod.embed_texts_cached(texts)

        assert fake.batch_calls == 1
        assert first == second
        assert all(client.get(k) is not None for k in keys)
    finally:
        for key in keys:
            client.delete(key)
