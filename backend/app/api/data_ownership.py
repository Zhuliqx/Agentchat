"""账号数据主权：导出 / 注销清理（认证路由与管理员路由共用）。

- ``build_user_export``：导出用户全部数据（用户信息 / 会话与消息 / 长期记忆 / 知识库文档）；
- ``purge_user_data``：注销/删除用户时清理知识库（向量 + 元数据）、长期记忆、checkpoint，
  再删除用户本身（级联删除 sessions/messages）。

从 auth 路由拆出，避免路由之间互相 import（原 admin 从 auth 导入 purge_user_data）。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.api.deps import is_admin_username
from app.config import settings
from app.db import postgres
from app.db.memory_store import cleanup_stale_checkpoints, get_store
from app.db.models import Document, Message, Session
from app.db.postgres import SessionLocal


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


async def build_user_export(user_id: str) -> dict:
    """导出用户全部数据（用户信息 / 会话与消息 / 长期记忆 / 知识库文档）。"""
    u = postgres.get_user(user_id)
    if not u:
        raise HTTPException(401, "用户不存在")

    user_out = {
        "id": u.id,
        "username": u.username,
        "avatar_color": u.avatar_color,
        "created_at": u.created_at.isoformat(),
        "is_admin": is_admin_username(u.username),
    }

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
        "user": user_out,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": sessions_out,
        "memories": memories,
        "documents": list(doc_map.values()),
    }
