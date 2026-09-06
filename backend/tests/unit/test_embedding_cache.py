"""embedding 缓存默认关闭路径：仍调用模型，不依赖 Redis。"""
from __future__ import annotations


class _FakeEmbedder:
    def __init__(self) -> None:
        self.query_calls = 0
        self.batch_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [1.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [[1.0] for _ in texts]


def test_embed_query_cached_disabled_does_not_need_redis(monkeypatch):
    """默认关闭时 query 缓存只走进程内 lru，不触碰 Redis。"""
    from app.config import settings
    from app.rag import embedding as embedding_mod

    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_mod, "get_embedder", lambda: fake)
    embedding_mod.embed_query_cached.cache_clear()
    try:
        embedding_mod.embed_query_cached("仅本地")
        assert fake.query_calls == 1
    finally:
        embedding_mod.embed_query_cached.cache_clear()


def test_embed_texts_cached_disabled_calls_model(monkeypatch):
    """默认关闭时批量 embedding 直接调模型，行为与 embed_texts 一致。"""
    from app.config import settings
    from app.rag import embedding as embedding_mod

    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_mod, "get_embedder", lambda: fake)

    out = embedding_mod.embed_texts_cached(["a", "b"])

    assert fake.batch_calls == 1
    assert out == [[1.0], [1.0]]
