"""Redis 客户端封装：默认关闭与不可达降级行为。

不依赖运行中的 Redis：默认禁用路径直接断言；启用但不可达路径只做本机
TCP 连接（连接被拒绝/超时即返回 ok=False），因此单元测试可离线运行。
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.cache.redis_client import (
    close_redis,
    get_redis,
    init_redis,
    redis_health,
    redis_key,
)


@pytest.fixture(autouse=True)
def _reset_redis_client():
    """每个用例前后关闭可能存在的连接，避免用例间互相污染。"""
    close_redis()
    yield
    close_redis()


def test_disabled_get_redis_returns_none_and_health_reports_disabled(monkeypatch):
    """Redis 默认关闭时：客户端不可用、健康状态明确标记 disabled。"""
    monkeypatch.setattr(settings, "redis_enabled", False)

    assert get_redis() is None
    assert redis_health() == {"enabled": False}


def test_enabled_unreachable_health_reports_not_ok(monkeypatch):
    """Redis 启用但连不上时：健康检查返回 ok=False，而不是抛异常。"""
    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(settings, "redis_port", 6399)
    monkeypatch.setattr(settings, "redis_password", "")
    monkeypatch.setattr(settings, "redis_db", 0)
    monkeypatch.setattr(settings, "redis_socket_timeout", 0.2)

    health = redis_health()

    assert health["enabled"] is True
    assert health["ok"] is False
    assert "error" in health


def test_init_redis_unreachable_does_not_raise(monkeypatch):
    """启动时 Redis 连不上只能告警，绝不能阻断应用启动。"""
    monkeypatch.setattr(settings, "redis_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "redis_host", "127.0.0.1")
    monkeypatch.setattr(settings, "redis_port", 6399)
    monkeypatch.setattr(settings, "redis_password", "")
    monkeypatch.setattr(settings, "redis_db", 0)
    monkeypatch.setattr(settings, "redis_socket_timeout", 0.2)

    init_redis()  # 不应抛异常


def test_redis_key_uses_configured_prefix(monkeypatch):
    """key 生成器必须统一应用配置前缀，供后续业务域隔离使用。"""
    monkeypatch.setattr(settings, "redis_key_prefix", "agentchat")
    assert redis_key("auth", "login_fail:alice") == "agentchat:auth:login_fail:alice"

    monkeypatch.setattr(settings, "redis_key_prefix", "custom")
    assert redis_key("ingest", "task:abc") == "custom:ingest:task:abc"
