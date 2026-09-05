"""认证与越权防护的单元测试（不依赖 Postgres/Milvus/LLM）。

覆盖：
- 聊天请求不能再由客户端指定 user_id（长期记忆/知识库隔离只认认证身份）；
- 自主任务会话 id 必须带当前用户前缀，禁止跨用户 history/confirm/resume；
- /api/tasks 与 /api/models/current 的“平台操作员”鉴权策略；
- JWT 过期校验；
- DB 查询 MCP 的表/列边界（users / langgraph 内部表）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


# ---------------- 聊天：user_id 客户端覆盖 ----------------

def test_chat_request_rejects_client_user_id():
    """聊天请求体不能再携带 user_id（旧客户端若发送应被 422 拒绝）。"""
    from pydantic import ValidationError

    from app.schemas.chat import ChatRequest

    with pytest.raises(ValidationError):
        ChatRequest(message="hello", user_id="victim-uid", use_rag=False, use_search=False)


def test_chat_routes_use_authenticated_user_id(monkeypatch):
    """chat 入口传给 Agent 的必须是认证用户，而不是请求体里的任何字段。"""
    from app.api.routes import chat as chat_mod
    from app.schemas.chat import ChatRequest

    captured: dict[str, object] = {}

    async def fake_prepare(_req, user_id):
        captured["prepared_user"] = user_id
        return "session-1", "msg-1"

    async def fake_run(**kwargs):
        captured["run_user"] = kwargs.get("user_id")
        captured["session_id"] = kwargs.get("session_id")
        return {"answer": "ok", "used_agents": [], "hitl_pending": None}

    async def fake_save(_session_id, _result):
        return None

    monkeypatch.setattr(chat_mod, "_prepare_context", fake_prepare)
    monkeypatch.setattr(chat_mod, "run_agent", fake_run)
    monkeypatch.setattr(chat_mod, "_save_assistant_if_final", fake_save)

    req = ChatRequest(message="hello", use_rag=False, use_search=False)
    # 模拟 FastAPI 依赖注入：Header 解析出的 alice
    result = chat_mod.chat(req, "alice")
    import asyncio

    asyncio.run(result)

    assert captured["prepared_user"] == "alice"
    assert captured["run_user"] == "alice"
    assert captured["session_id"] == "session-1"


def test_chat_stream_uses_authenticated_user_id(monkeypatch):
    """SSE 聊天入口同样只把认证用户传给 Agent。"""
    from app.api.routes import chat as chat_mod
    from app.schemas.chat import ChatRequest

    captured: dict[str, object] = {}

    async def fake_prepare(_req, user_id):
        captured["prepared_user"] = user_id
        return "session-2", "msg-2"

    async def fake_stream(**kwargs):
        captured["stream_user"] = kwargs.get("user_id")
        return {
            "answer": "ok",
            "used_agents": [],
            "hitl_pending": None,
            "sources": ["platform_doc_a.txt"],
        }

    async def fake_save(_session_id, _result):
        return "asst-1"

    monkeypatch.setattr(chat_mod, "_prepare_context", fake_prepare)
    monkeypatch.setattr(chat_mod, "stream_agent", fake_stream)
    monkeypatch.setattr(chat_mod, "_save_assistant_if_final", fake_save)

    req = ChatRequest(message="hello", use_rag=False, use_search=False)

    async def consume():
        resp = await chat_mod.chat_stream(req, "alice")
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return chunks

    import asyncio

    out = asyncio.run(consume())
    assert captured["prepared_user"] == "alice"
    assert captured["stream_user"] == "alice"
    assert any('"type": "message"' in c for c in out)
    assert any("platform_doc_a.txt" in c for c in out)


# ---------------- 认证：带过期/无效 token 不得静默降级访客 ----------------

def test_no_auth_header_returns_guest(monkeypatch):
    from app.config import settings

    deps = _deps()
    assert deps.get_current_user_id(None) == settings.guest_user_id


def test_invalid_bearer_raises_401_instead_of_guest(monkeypatch):
    deps = _deps()
    with pytest.raises(HTTPException) as exc:
        deps.get_current_user_id("Bearer invalid.token.here")
    assert exc.value.status_code == 401


def test_valid_bearer_returns_user(monkeypatch):
    deps = _deps()
    monkeypatch.setattr(deps, "decode_token", lambda token: "uid-alice")
    assert deps.get_current_user_id("Bearer good-token") == "uid-alice"


# ---------------- 自主任务：会话归属 ----------------

def _task_module():
    from app.api.routes import task_agent

    return task_agent


def test_new_task_thread_has_user_prefix():
    mod = _task_module()
    tid = mod.build_task_thread_id("alice")
    assert tid.startswith("alice:")


def test_owned_task_thread_accepts_own_session():
    mod = _task_module()
    tid = mod.build_task_thread_id("alice")
    assert mod.ensure_task_thread_owned(tid, "alice") == tid


def test_owned_task_thread_rejects_other_user():
    mod = _task_module()
    with pytest.raises(HTTPException) as exc:
        mod.ensure_task_thread_owned("bob:task-abc123", "alice")
    assert exc.value.status_code == 404


def test_owned_task_thread_rejects_legacy_unprefixed_session():
    mod = _task_module()
    with pytest.raises(HTTPException) as exc:
        mod.ensure_task_thread_owned("task-abc123", "alice")
    assert exc.value.status_code == 404


# ---------------- 平台操作员鉴权（tasks / models） ----------------

def _deps():
    from app.api import deps

    return deps


def test_platform_operator_guest_only_instance_allows_anonymous(monkeypatch):
    """默认单用户模式（库中只有访客）保持零配置可用。"""
    from app.config import settings

    deps = _deps()
    from app.db import postgres

    monkeypatch.setattr(postgres, "has_registered_users", lambda: False)
    assert deps.require_platform_operator(None) == settings.guest_user_id


def test_platform_operator_denies_anonymous_when_users_exist(monkeypatch):
    deps = _deps()
    from app.db import postgres

    monkeypatch.setattr(postgres, "has_registered_users", lambda: True)
    with pytest.raises(HTTPException) as exc:
        deps.require_platform_operator(None)
    assert exc.value.status_code in (401, 403)


def test_platform_operator_denies_non_admin_login(monkeypatch):
    deps = _deps()
    from app.config import settings
    from app.db import postgres

    monkeypatch.setattr(deps, "decode_token", lambda token: "uid-bob")
    monkeypatch.setattr(
        postgres, "get_user", lambda uid: type("U", (), {"username": "bob"})()
    )
    monkeypatch.setattr(postgres, "has_registered_users", lambda: True)
    monkeypatch.setattr(settings, "admin_usernames", "admin")
    with pytest.raises(HTTPException) as exc:
        deps.require_platform_operator("Bearer good-token")
    assert exc.value.status_code == 403


def test_platform_operator_allows_admin_login(monkeypatch):
    deps = _deps()
    from app.config import settings
    from app.db import postgres

    monkeypatch.setattr(deps, "decode_token", lambda token: "uid-admin")
    monkeypatch.setattr(
        postgres, "get_user", lambda uid: type("U", (), {"username": "admin"})()
    )
    monkeypatch.setattr(postgres, "has_registered_users", lambda: True)
    monkeypatch.setattr(settings, "admin_usernames", "admin")
    assert deps.require_platform_operator("Bearer admin-token") == "uid-admin"


def test_is_platform_operator_flag_matches_require(monkeypatch):
    """前端能力查询应与 require_platform_operator 同策略。"""
    from app.config import settings
    from app.db import postgres

    deps = _deps()
    monkeypatch.setattr(postgres, "has_registered_users", lambda: False)
    assert deps.is_platform_operator(None) is True

    monkeypatch.setattr(postgres, "has_registered_users", lambda: True)
    assert deps.is_platform_operator(None) is False

    monkeypatch.setattr(deps, "decode_token", lambda token: "uid-admin")
    monkeypatch.setattr(
        postgres, "get_user", lambda uid: type("U", (), {"username": "admin"})()
    )
    monkeypatch.setattr(settings, "admin_usernames", "admin")
    assert deps.is_platform_operator("Bearer admin-token") is True


def test_login_throttle_blocks_and_expires(monkeypatch):
    """登录失败 5 次后拦截，窗口过期后恢复。"""
    from app.config import settings
    from app.api.routes import auth as auth_mod

    # 单元测试固定走进程内实现：now 参数模拟过期不依赖真实时钟
    monkeypatch.setattr(settings, "redis_enabled", False)

    user = "throttle-test-user"
    try:
        now = 1000.0
        for i in range(auth_mod._LOGIN_MAX_FAILURES):
            auth_mod.record_login_failure(user, now=now + i)
        assert auth_mod.is_login_blocked(user, now=now + 10) is True
        # 窗口过期（从最后一次失败算 5 分钟）后不再拦截
        last_failure = now + auth_mod._LOGIN_MAX_FAILURES - 1
        assert (
            auth_mod.is_login_blocked(
                user, now=last_failure + auth_mod._LOGIN_WINDOW_SEC + 1
            )
            is False
        )
    finally:
        auth_mod.clear_login_failures(user)


# ---------------- JWT 过期 ----------------

def test_jwt_has_exp_and_respects_ttl(monkeypatch):
    from app.config import settings
    from app.security import create_token

    monkeypatch.setattr(settings, "auth_secret", "test-secret-0123456789abcdefghijklmn")
    monkeypatch.setattr(settings, "access_token_ttl_seconds", 3600)
    token = create_token("user-1")

    import jwt

    payload = jwt.decode(
        token,
        settings.auth_secret.encode(),
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert payload["exp"] - payload["iat"] == 3600


def test_jwt_expired_token_rejected(monkeypatch):
    from app.config import settings
    from app.security import create_token, decode_token

    monkeypatch.setattr(settings, "auth_secret", "test-secret-0123456789abcdefghijklmn")
    token = create_token("user-1", ttl_seconds=-10)
    assert decode_token(token) is None


# ---------------- DB MCP 表边界 ----------------

def _validator():
    from app.mcp_integration.servers.db_query_server import _validate_readonly

    return _validate_readonly


def test_db_query_denies_auth_and_langgraph_tables():
    validate = _validator()
    for sql in [
        "SELECT * FROM users",
        "SELECT username, password_hash FROM users",
        "SELECT * FROM app_settings",
        "SELECT * FROM checkpoints",
        "SELECT * FROM checkpoint_writes",
        "SELECT * FROM store",
    ]:
        assert validate(sql) is not None, f"应拒绝: {sql}"


def test_db_query_still_allows_business_tables():
    validate = _validator()
    assert validate("SELECT * FROM sessions") is None
    assert validate("SELECT * FROM messages") is None
    assert validate("SELECT * FROM documents") is None
    assert validate("SELECT * FROM tasks") is None
