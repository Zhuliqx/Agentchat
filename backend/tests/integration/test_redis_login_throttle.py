"""登录限速 Redis 集成测试（需运行中的 Redis）。

验证失败计数真正写入 Redis（而不是仍留在进程内），并带上统一 key 前缀与
窗口 TTL；这样多 worker 部署时各进程共享同一份计数。
"""
from __future__ import annotations

import uuid

import pytest

from helpers import redis_available

pytestmark = pytest.mark.skipif(
    not redis_available(), reason="需要运行中的 Redis"
)


def _unique_user() -> str:
    return f"redis-throttle-{uuid.uuid4().hex[:10]}"


def _enable_redis(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "redis_enabled", True)


def test_login_failures_recorded_in_redis_and_clear(monkeypatch):
    """失败计数必须写入 Redis：计数达上限后拦截，成功清空后恢复。"""
    from app.api.routes import auth as auth_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user = _unique_user()
    client = get_redis()
    assert client is not None
    key = redis_key("auth", "login_fail", user)
    client.delete(key)
    try:
        for _ in range(auth_mod._LOGIN_MAX_FAILURES):
            auth_mod.record_login_failure(user)

        assert auth_mod.is_login_blocked(user) is True
        assert int(client.get(key)) == auth_mod._LOGIN_MAX_FAILURES

        auth_mod.clear_login_failures(user)
        assert auth_mod.is_login_blocked(user) is False
        assert client.get(key) is None
    finally:
        auth_mod.clear_login_failures(user)
        client.delete(key)


def test_login_failure_key_expires_with_window(monkeypatch):
    """Redis key 必须带窗口 TTL，避免失败计数永久残留。"""
    from app.api.routes import auth as auth_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user = _unique_user()
    client = get_redis()
    assert client is not None
    key = redis_key("auth", "login_fail", user)
    client.delete(key)
    try:
        auth_mod.record_login_failure(user)
        ttl = client.ttl(key)
        assert 0 < ttl <= auth_mod._LOGIN_WINDOW_SEC
    finally:
        auth_mod.clear_login_failures(user)
        client.delete(key)
