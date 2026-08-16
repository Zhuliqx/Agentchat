"""用户注册 / 登录 / 当前用户接口（JWT 认证）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_user_id
from app.db import postgres
from app.security import create_token, hash_password, verify_password

router = APIRouter()


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, pattern=r"^[\w.\-]+$")
    password: str = Field(..., min_length=6, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    username: str
    created_at: str


class LoginOut(BaseModel):
    token: str
    user: UserOut


def _user_out(u) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        created_at=u.created_at.isoformat(),
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterIn):
    if postgres.get_user_by_username(body.username):
        raise HTTPException(409, "用户名已存在")
    u = postgres.create_user(body.username, hash_password(body.password))
    return _user_out(u)


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn):
    u = postgres.get_user_by_username(body.username)
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return LoginOut(token=create_token(u.id), user=_user_out(u))


@router.get("/me", response_model=UserOut)
def me(user_id: str | None = Depends(require_user_id)):
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")
    return _user_out(u)


@router.get("/stats")
async def stats(user_id: str | None = Depends(require_user_id)) -> dict:
    """个人主页聚合统计：会话数、消息数、记忆数、文档数等。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")

    session_count = postgres.count_sessions(user_id)
    message_count = postgres.count_messages_for_user(user_id)
    document_count = postgres.count_documents()

    memory_count = 0
    try:
        from app.db.memory_store import get_store

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
