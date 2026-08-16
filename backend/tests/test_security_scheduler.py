"""认证(JWT/密码)与任务调度逻辑单测，不依赖外部服务。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.security import create_token, decode_token, hash_password, verify_password


# ---------------- 密码 ----------------

def test_password_hash_roundtrip():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h)


def test_password_hash_unique_salt():
    assert hash_password("same") != hash_password("same")


def test_verify_password_bad_format():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "")


# ---------------- JWT ----------------

def test_token_roundtrip(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token = create_token("user-1")
    assert decode_token(token) == "user-1"


def test_token_invalid(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    assert decode_token("garbage.token.here") is None


def test_token_wrong_secret(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_secret", "test-secret")
    token = create_token("user-1")
    monkeypatch.setattr(settings, "auth_secret", "other-secret")
    assert decode_token(token) is None


# ---------------- 调度表达式 ----------------

def test_interval_schedule():
    from app.scheduler import compute_next_run

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = compute_next_run("interval:3600", now)
    assert nxt == now + timedelta(seconds=3600)
    assert compute_next_run("interval:0", now) is None
    assert compute_next_run("interval:abc", now) is None


def test_cron_schedule():
    from app.scheduler import compute_next_run

    now = datetime(2026, 1, 1, 12, 7, 30, tzinfo=timezone.utc)
    # */10：对齐到下一个 10 分钟点（12:10）
    assert compute_next_run("cron:*/10", now) == datetime(2026, 1, 1, 12, 10, 0, tzinfo=timezone.utc)
    # 具体分钟 0：本小时已过 → 下一小时整点
    assert compute_next_run("cron:0", now) == datetime(2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
    # 具体分钟 30：本小时未到 → 12:30
    assert compute_next_run("cron:30", now) == datetime(2026, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    assert compute_next_run("cron:*", now) is not None
    assert compute_next_run("cron:bad", now) is None


def test_registry_has_expected_tasks():
    from app.scheduler import TASK_REGISTRY

    assert {"reindex_documents", "cleanup_checkpoints", "vacuum_documents"} <= set(TASK_REGISTRY)
