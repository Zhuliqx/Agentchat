"""用户注册 / 登录 / 当前用户接口（JWT 认证）。"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.data_ownership import build_user_export, purge_user_data
from app.api.deps import is_admin_username, is_platform_operator, require_user_id
from app.cache.redis_client import get_redis, redis_key
from app.db import postgres
from app.db import token_store
from app.db.memory_store import get_store
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token_payload,
    hash_password,
    verify_password,
)

router = APIRouter()

# 登录限速（进程内、尽力而为）：同用户名 5 分钟窗口最多失败 5 次
_LOGIN_WINDOW_SEC = 300.0
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[str, list[float]] = {}


def _login_failure_key(username: str) -> str:
    """登录失败计数的 Redis key（带统一前缀与业务域）。"""
    return redis_key("auth", "login_fail", username)


def _prune_login_failures(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    expired = [
        name
        for name, ts in _LOGIN_FAILURES.items()
        if ts and max(ts) < now - _LOGIN_WINDOW_SEC
    ]
    for name in expired:
        _LOGIN_FAILURES.pop(name, None)


def is_login_blocked(username: str, now: float | None = None) -> bool:
    """用户名在当前窗口内失败次数是否已达上限。"""
    client = get_redis()
    if client is not None:
        try:
            value = client.get(_login_failure_key(username))
            return value is not None and int(value) >= _LOGIN_MAX_FAILURES
        except Exception:  # noqa: BLE001
            pass  # Redis 故障时退回进程内计数，避免登录接口不可用
    with _LOGIN_LOCK:
        _prune_login_failures(now)
        return len(_LOGIN_FAILURES.get(username, [])) >= _LOGIN_MAX_FAILURES


def record_login_failure(username: str, now: float | None = None) -> None:
    client = get_redis()
    if client is not None:
        try:
            key = _login_failure_key(username)
            pipe = client.pipeline()
            pipe.incr(key)
            # NX：只在首次失败时起算窗口，后续失败不延长锁定期
            pipe.expire(key, int(_LOGIN_WINDOW_SEC), nx=True)
            pipe.execute()
            return
        except Exception:  # noqa: BLE001
            pass  # Redis 故障时退回进程内计数（尽力而为）
    with _LOGIN_LOCK:
        _prune_login_failures(now)
        _LOGIN_FAILURES.setdefault(username, []).append(
            time.monotonic() if now is None else now
        )


def clear_login_failures(username: str) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.delete(_login_failure_key(username))
        except Exception:  # noqa: BLE001
            pass
    # 同时清理进程内计数：覆盖 Redis 故障期间或启用前的残留
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(username, None)


@router.get("/capabilities")
def capabilities(authorization: str | None = Header(default=None)) -> dict:
    """返回当前身份是否具备平台操作员能力（模型切换/定时任务入口用）。"""
    return {"platform_operator": is_platform_operator(authorization)}


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, pattern=r"^[\w.\-]+$")
    password: str = Field(..., min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UpdateProfileIn(BaseModel):
    """修改用户名/头像颜色（可选字段；至少提供一项）。"""

    username: str | None = Field(
        default=None, min_length=2, max_length=32, pattern=r"^[\w.\-]+$"
    )
    avatar_color: str | None = Field(default=None, pattern=r"^[a-z]+$")


class ChangePasswordIn(BaseModel):
    """修改密码：需验证旧密码。"""

    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class UserOut(BaseModel):
    id: str
    username: str
    avatar_color: str
    created_at: str
    is_admin: bool = False


class LoginOut(BaseModel):
    token: str
    refresh_token: str
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=4096)


def _user_out(u) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        avatar_color=u.avatar_color,
        created_at=u.created_at.isoformat(),
        is_admin=is_admin_username(u.username),
    )


def _issue_token_pair(user_id: str) -> tuple[str, str]:
    """签发 access + refresh，并把 refresh 登记到 token_sessions。"""
    access = create_access_token(user_id)
    refresh, jti = create_refresh_token(user_id)
    payload = decode_token_payload(refresh) or {}
    expires_at = datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
    token_store.store_refresh_token(user_id, jti, expires_at)
    return access, refresh


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn):
    if postgres.get_user_by_username(body.username):
        raise HTTPException(409, "用户名已存在")
    u = postgres.create_user(body.username, hash_password(body.password))
    return _user_out(u)


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn):
    if is_login_blocked(body.username):
        raise HTTPException(429, "失败次数过多，请稍后再试")
    u = postgres.get_user_by_username(body.username)
    if not u or not verify_password(body.password, u.password_hash):
        record_login_failure(body.username)
        raise HTTPException(401, "用户名或密码错误")
    clear_login_failures(body.username)
    access, refresh = _issue_token_pair(u.id)
    return LoginOut(token=access, refresh_token=refresh, user=_user_out(u))


@router.post("/refresh", response_model=LoginOut)
def refresh_token(body: RefreshIn):
    """用 refresh token 换取新的 access+refresh（旧 refresh 轮换作废）。"""
    payload = decode_token_payload(body.refresh_token)
    if not payload or payload.get("type") != "refresh" or not payload.get("jti"):
        raise HTTPException(401, "refresh token 无效")
    row = token_store.get_refresh_token(payload["jti"])
    if not row or row.get("revoked_at") is not None:
        raise HTTPException(401, "refresh token 已失效")
    expires_at = row.get("expires_at")
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        raise HTTPException(401, "refresh token 已过期")
    user = postgres.get_user(row["user_id"])
    if not user:
        raise HTTPException(401, "用户不存在")
    token_store.revoke_refresh_token(payload["jti"])  # 轮换：旧 token 立即失效
    access, refresh = _issue_token_pair(user.id)
    return LoginOut(token=access, refresh_token=refresh, user=_user_out(user))


@router.post("/logout", status_code=204)
def logout(body: RefreshIn):
    """注销 refresh token（后续无法续期；已签发的 access 到期自然失效）。"""
    payload = decode_token_payload(body.refresh_token)
    if payload and payload.get("type") == "refresh" and payload.get("jti"):
        token_store.revoke_refresh_token(payload["jti"])
    return None


@router.get("/me", response_model=UserOut)
def me(user_id: str | None = Depends(require_user_id)):
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")
    return _user_out(u)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: UpdateProfileIn, user_id: str | None = Depends(require_user_id)
):
    """修改用户名/头像颜色（重复用户名返回 409）。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    if body.username is None and body.avatar_color is None:
        raise HTTPException(400, "缺少要修改的字段")
    if body.username is not None and postgres.get_user_by_username(body.username):
        raise HTTPException(409, "用户名已存在")
    u = postgres.update_user(
        user_id, username=body.username, avatar_color=body.avatar_color
    )
    if not u:
        raise HTTPException(401, "用户不存在")
    return _user_out(u)


@router.put("/password", response_model=UserOut)
def change_password(
    body: ChangePasswordIn, user_id: str | None = Depends(require_user_id)
):
    """修改密码：先校验旧密码，再更新为新密码哈希。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")
    if not verify_password(body.old_password, u.password_hash):
        raise HTTPException(400, "旧密码错误")
    postgres.update_user(user_id, password_hash=hash_password(body.new_password))
    token_store.revoke_user_tokens(user_id)  # 改密后旧 refresh 全部失效
    return _user_out(postgres.get_user(user_id))


@router.get("/stats")
async def stats(user_id: str | None = Depends(require_user_id)) -> dict:
    """个人主页聚合统计：会话数、消息数、记忆数、文档数等。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")

    session_count = await asyncio.to_thread(postgres.count_sessions, user_id)
    message_count = await asyncio.to_thread(
        postgres.count_messages_for_user, user_id
    )
    document_count = await asyncio.to_thread(postgres.count_documents)

    memory_count = 0
    try:
        store = get_store()
        if store is not None:
            items = await store.asearch((user_id, "memories"), limit=1000)
            memory_count = len(items)
    except Exception:
        pass

    return {
        "username": u.username,
        "created_at": u.created_at.isoformat(),
        "session_count": session_count,
        "message_count": message_count,
        "memory_count": memory_count,
        "document_count": document_count,
        "token_estimate": max(1, message_count * 200),
    }


# ---------------- 数据导出 / 账号注销 ----------------


@router.get("/export")
async def export_data(user_id: str | None = Depends(require_user_id)) -> dict:
    """导出当前用户全部数据（用户信息 / 会话与消息 / 长期记忆 / 知识库文档）。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    return await build_user_export(user_id)


@router.delete("/me", status_code=204)
async def delete_account(user_id: str | None = Depends(require_user_id)):
    """注销账号：清理知识库（向量+元数据）、长期记忆、checkpoint 后删除用户（级联会话/消息）。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    await purge_user_data(user_id)
