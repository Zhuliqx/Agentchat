"""BM25 文档集签名缓存 Redis 集成测试（需 Postgres + Redis）。

签名缓存的目标是让多 worker 共享同一份“用户文档行数 + 最新创建时间”，
摄入/删除后按用户失效，避免某个 worker 继续用旧签名重建 BM25。
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from helpers import postgres_available, redis_available

pytestmark = pytest.mark.skipif(
    not (redis_available() and postgres_available()),
    reason="需要运行中的 Postgres + Redis",
)


def _enable_redis(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "redis_enabled", True)


def _cleanup(hybrid_mod, client, key: str, user_id: str) -> None:
    client.delete(key)
    with hybrid_mod._signature_lock:
        hybrid_mod._signature_cache.pop(user_id, None)


def test_docs_signature_reads_from_redis(monkeypatch):
    """签名必须从 Redis 读取：另一个 worker 写入的快照能被本进程读到。"""
    from app.rag import hybrid as hybrid_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user_id = f"sig-user-{uuid.uuid4().hex[:8]}"
    key = redis_key("rag", "sig", user_id)
    client = get_redis()
    assert client is not None
    client.delete(key)
    try:
        hybrid_mod._docs_signature(user_id)
        assert client.get(key) is not None, "签名未写入 Redis"

        # 写入一个“另一个 worker 计算出的”签名，然后清空本进程内存
        hybrid_mod.invalidate_docs_signature(user_id)
        client.set(
            key,
            '[42, "2026-09-05T08:00:00+00:00"]',
        )
        with hybrid_mod._signature_lock:
            hybrid_mod._signature_cache.pop(user_id, None)

        count, latest = hybrid_mod._docs_signature(user_id)
        assert count == 42
        assert latest == datetime(2026, 9, 5, 8, 0, tzinfo=timezone.utc)
    finally:
        _cleanup(hybrid_mod, client, key, user_id)


def test_invalidate_docs_signature_deletes_redis_key(monkeypatch):
    """按用户失效时，Redis 中的签名 key 必须一并删除。"""
    from app.rag import hybrid as hybrid_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user_id = f"sig-invalidate-{uuid.uuid4().hex[:8]}"
    key = redis_key("rag", "sig", user_id)
    client = get_redis()
    assert client is not None
    client.delete(key)
    try:
        hybrid_mod._docs_signature(user_id)
        assert client.get(key) is not None

        hybrid_mod.invalidate_docs_signature(user_id)
        assert client.get(key) is None
    finally:
        _cleanup(hybrid_mod, client, key, user_id)


def test_docs_signature_key_expires_with_ttl(monkeypatch):
    """签名 key 必须带短 TTL，避免 Redis 中长期残留旧签名。"""
    from app.rag import hybrid as hybrid_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user_id = f"sig-ttl-{uuid.uuid4().hex[:8]}"
    key = redis_key("rag", "sig", user_id)
    client = get_redis()
    assert client is not None
    client.delete(key)
    try:
        hybrid_mod._docs_signature(user_id)
        ttl = client.ttl(key)
        assert 0 < ttl <= hybrid_mod._SIGNATURE_TTL
    finally:
        _cleanup(hybrid_mod, client, key, user_id)
