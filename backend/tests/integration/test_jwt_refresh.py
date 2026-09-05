"""JWT access/refresh 与撤销集成测试（需 Postgres）。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from helpers import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(), reason="需要运行中的 Postgres"
)


def _register(client, name: str, password: str = "pw-123456") -> tuple[str, str]:
    r = client.post("/api/auth/register", json={"username": name, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["id"], password


def _login(client, name: str, password: str = "pw-123456") -> dict:
    r = client.post("/api/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_refresh_rotates_and_old_refresh_invalidated():
    from app.db.models import User
    from app.db.postgres import SessionLocal

    name = f"jwt_{uuid.uuid4().hex[:8]}"
    uid = None
    try:
        with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
            uid, _ = _register(client, name)
            tokens = _login(client, name)
            access = tokens.get("token")
            refresh = tokens.get("refresh_token")
            assert access and refresh, "登录应返回 access + refresh"

            me = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {access}"},
            )
            assert me.status_code == 200

            rotated = client.post("/api/auth/refresh", json={"refresh_token": refresh})
            assert rotated.status_code == 200, rotated.text
            new_access = rotated.json()["token"]
            new_refresh = rotated.json()["refresh_token"]
            assert new_refresh != refresh
            assert new_access != access

            # 旧 refresh 已被轮换作废
            reuse = client.post("/api/auth/refresh", json={"refresh_token": refresh})
            assert reuse.status_code == 401

            # 新 refresh 仍可用；登出后作废
            me2 = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {new_access}"},
            )
            assert me2.status_code == 200
            logout = client.post("/api/auth/logout", json={"refresh_token": new_refresh})
            assert logout.status_code == 204
            after = client.post(
                "/api/auth/refresh", json={"refresh_token": new_refresh}
            )
            assert after.status_code == 401
    finally:
        if uid:
            with SessionLocal() as session:
                u = session.get(User, uid)
                if u:
                    session.delete(u)
                session.commit()


def test_password_change_revokes_refresh_tokens():
    from app.db.models import User
    from app.db.postgres import SessionLocal

    name = f"jwt_pw_{uuid.uuid4().hex[:8]}"
    uid = None
    try:
        with TestClient(__import__("app.main", fromlist=["app"]).app) as client:
            uid, password = _register(client, name)
            tokens = _login(client, name, password)
            old_refresh = tokens["refresh_token"]

            headers = {"Authorization": f"Bearer {tokens['token']}"}
            changed = client.put(
                "/api/auth/password",
                json={"old_password": password, "new_password": "new-pass-123"},
                headers=headers,
            )
            assert changed.status_code == 200, changed.text

            refresh_after = client.post(
                "/api/auth/refresh", json={"refresh_token": old_refresh}
            )
            assert refresh_after.status_code == 401
    finally:
        if uid:
            with SessionLocal() as session:
                u = session.get(User, uid)
                if u:
                    session.delete(u)
                session.commit()
