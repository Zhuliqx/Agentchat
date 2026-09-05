"""Redis 客户端集成测试（需运行中的 Redis）。

与单元测试不同，这里验证 init_redis / redis_health / get_redis 在真实
Redis 服务上成立；Redis 不可达或未配置时自动跳过整个模块。
"""
from __future__ import annotations

import pytest

from helpers import redis_available

pytestmark = pytest.mark.skipif(
    not redis_available(), reason="需要运行中的 Redis"
)


def test_init_health_and_get_with_real_redis(monkeypatch):
    """真实 Redis：init 后健康 ok，且 get_redis 返回同一可用客户端。"""
    from app.config import settings
    from app.cache.redis_client import (
        close_redis,
        get_redis,
        init_redis,
        redis_health,
    )

    monkeypatch.setattr(settings, "redis_enabled", True)
    try:
        init_redis()
        assert redis_health() == {"enabled": True, "ok": True}
        client = get_redis()
        assert client is not None
        assert client.ping() is True
    finally:
        close_redis()
