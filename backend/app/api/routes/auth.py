"""用户注册 / 登录 / 当前用户接口（JWT 认证）。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import is_admin_username, require_user_id
from app.config import settings
from app.db import postgres
from app.db.memory_store import cleanup_stale_checkpoints, get_store
from app.db.models import Document, Message, Session
from app.db.postgres import SessionLocal
from app.security import create_token, hash_password, verify_password

router = APIRouter()


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
    user: UserOut


def _user_out(u) -> UserOut:
    return UserOut(
        id=u.id,
        username=u.username,
        avatar_color=u.avatar_color,
        created_at=u.created_at.isoformat(),
        is_admin=is_admin_username(u.username),
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
    return _user_out(postgres.get_user(user_id))


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


# ---------------- 数据导出 / 账号注销 ----------------

def _delete_user_vectors(user_id: str) -> None:
    """删除该用户在 Milvus 中的全部向量（按 source 去重逐个清理）。"""
    from app.rag import vector_store

    client = vector_store._client()
    rows = client.query(
        vector_store.settings.milvus_collection,
        filter=f'user_id == "{user_id}"',
        output_fields=["source"],
        limit=16384,
    )
    seen: set[str] = set()
    for r in rows:
        src = r.get("source")
        if src and src not in seen:
            seen.add(src)
            vector_store.delete_by_source(src, user_id=user_id)


async def _delete_user_memories(user_id: str) -> None:
    """删除该用户的全部长期记忆（store 命名空间）。"""
    store = get_store()
    if store is None:
        return
    items = await store.asearch((user_id, "memories"), limit=1000)
    for it in items:
        await store.adelete((user_id, "memories"), it.key)


async def purge_user_data(user_id: str) -> None:
    """清理用户全部数据（向量/文档元数据/记忆/checkpoint）并删除用户本身。

    供「注销账号」与「管理员删除用户」复用；访客账号拒绝删除。
    """
    if user_id == settings.guest_user_id:
        raise HTTPException(400, "访客账号不可删除")
    if not postgres.get_user(user_id):
        raise HTTPException(404, "用户不存在")

    # 删除前收集该用户会话 id（用于定向清理 LangGraph checkpoint）
    with SessionLocal() as db:
        session_ids = [
            s.id for s in db.query(Session).filter(Session.user_id == user_id).all()
        ]

    # 1) 知识库：Milvus 向量 + Postgres 元数据
    try:
        _delete_user_vectors(user_id)
    except Exception:
        pass
    with SessionLocal() as db:
        db.query(Document).filter(Document.user_id == user_id).delete()
        db.commit()
    # 2) 长期记忆
    try:
        await _delete_user_memories(user_id)
    except Exception:
        pass
    # 3) 会话 checkpoint（孤儿线程）
    if session_ids:
        cleanup_stale_checkpoints(session_ids)
    # 4) 删除用户（级联删除 sessions/messages）
    postgres.delete_user(user_id)


@router.get("/export")
async def export_data(user_id: str | None = Depends(require_user_id)) -> dict:
    """导出当前用户全部数据（用户信息 / 会话与消息 / 长期记忆 / 知识库文档）。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")

    sessions_out = []
    with SessionLocal() as db:
        sessions = (
            db.query(Session)
            .filter(Session.user_id == user_id)
            .order_by(Session.updated_at.desc())
            .all()
        )
        for s in sessions:
            msgs = (
                db.query(Message)
                .filter(Message.session_id == s.id)
                .order_by(Message.created_at.asc())
                .all()
            )
            sessions_out.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "created_at": s.created_at.isoformat(),
                    "updated_at": (
                        s.updated_at.isoformat() if s.updated_at else None
                    ),
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content,
                            "created_at": m.created_at.isoformat(),
                        }
                        for m in msgs
                    ],
                }
            )
        # 知识库文档（按 source 聚合）
        doc_map: dict[str, dict] = {}
        for d in (
            db.query(Document)
            .filter(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .all()
        ):
            g = doc_map.setdefault(
                d.source,
                {
                    "filename": d.filename,
                    "source": d.source,
                    "chunks": 0,
                    "created_at": d.created_at.isoformat(),
                },
            )
            g["chunks"] += 1

    # 长期记忆
    memories = []
    store = get_store()
    if store is not None:
        try:
            items = await store.asearch((user_id, "memories"), limit=1000)
            for it in items:
                memories.append(
                    {
                        "id": it.key,
                        "content": (it.value or {}).get("content", ""),
                        "created_at": (
                            it.created_at.isoformat() if it.created_at else None
                        ),
                    }
                )
        except Exception:
            pass

    return {
        "user": _user_out(u).model_dump(),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": sessions_out,
        "memories": memories,
        "documents": list(doc_map.values()),
    }


@router.delete("/me", status_code=204)
async def delete_account(user_id: str | None = Depends(require_user_id)):
    """注销账号：清理知识库（向量+元数据）、长期记忆、checkpoint 后删除用户（级联会话/消息）。"""
    if not user_id:
        raise HTTPException(401, "未登录")
    await purge_user_data(user_id)
