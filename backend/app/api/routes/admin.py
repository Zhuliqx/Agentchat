"""管理员后台接口（需 require_admin：用户名在 ADMIN_USERNAMES 中）。

提供平台统计、用户列表与用户删除；删除用户复用 data_ownership.purge_user_data
（向量/文档/记忆/checkpoint/用户级联清理）。

知识库在线检索评估（/eval*）：案例生成/持久化与判定逻辑在
``app.evaluation.kb_eval``，本路由只做请求编排。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from app.api.data_ownership import purge_user_data
from app.api.deps import is_admin_username, require_admin
from app.config import settings
from app.db import postgres
from app.db.models import Document, Message, Session, User
from app.db.postgres import SessionLocal
from app.db.runtime_settings import get_runtime_settings, save_runtime_settings
from app.evaluation.kb_eval import (
    _auto_cases,
    _basename,
    _BUILTIN_CASES,
    _doc_sources,
    _load_custom_cases,
    _save_custom_cases,
    run_kb_eval,
)

router = APIRouter()


@router.get("/stats")
def admin_stats(_admin_id: str = Depends(require_admin)) -> dict:
    """平台整体统计（用户/会话/消息/文档）。"""
    with SessionLocal() as db:
        return {
            "user_count": db.query(func.count(User.id)).scalar() or 0,
            "session_count": db.query(func.count(Session.id)).scalar() or 0,
            "message_count": db.query(func.count(Message.id)).scalar() or 0,
            "document_count": db.query(func.count(Document.id)).scalar() or 0,
        }


@router.get("/usage")
def admin_usage(_admin_id: str = Depends(require_admin)) -> dict:
    """按天聚合消息量（最近 14 天），并估算 token 消耗。"""
    with SessionLocal() as db:
        day = func.date(func.timezone("UTC", Message.created_at))
        rows = (
            db.query(day, func.count(Message.id)).group_by(day).order_by(day).all()
        )
    items = [
        {"date": str(d), "messages": int(c), "tokens": int(c) * 200}
        for d, c in rows
    ]
    return {
        "items": items[-14:],
        "total_messages": sum(x["messages"] for x in items),
        "total_tokens": sum(x["tokens"] for x in items),
    }


class SettingsIn(BaseModel):
    """保存系统设置请求体（key -> 原始值字符串/数字）。"""

    values: dict[str, str | int | float | bool]


@router.get("/settings")
def admin_get_settings(_admin_id: str = Depends(require_admin)) -> dict:
    """返回可在线调整的系统设置（检索/生成相关）。"""
    return {"items": get_runtime_settings()}


@router.put("/settings")
def admin_save_settings(
    body: SettingsIn, _admin_id: str = Depends(require_admin)
) -> dict:
    """保存系统设置到 DB 并立即生效。"""
    return {"items": save_runtime_settings(body.values)}


@router.get("/users")
def admin_users(_admin_id: str = Depends(require_admin)) -> list[dict]:
    """全部用户列表（含各用户会话/消息/文档统计与管理员标记）。"""
    with SessionLocal() as db:
        users = db.query(User).order_by(User.created_at.asc()).all()
        out = []
        for u in users:
            session_count = (
                db.query(func.count(Session.id))
                .filter(Session.user_id == u.id)
                .scalar()
                or 0
            )
            message_count = (
                db.query(func.count(Message.id))
                .join(Session, Message.session_id == Session.id)
                .filter(Session.user_id == u.id)
                .scalar()
                or 0
            )
            document_count = (
                db.query(func.count(Document.id))
                .filter(Document.user_id == u.id)
                .scalar()
                or 0
            )
            out.append(
                {
                    "id": u.id,
                    "username": u.username,
                    "avatar_color": u.avatar_color,
                    "created_at": u.created_at.isoformat(),
                    "is_admin": is_admin_username(u.username),
                    "session_count": session_count,
                    "message_count": message_count,
                    "document_count": document_count,
                }
            )
        return out


@router.delete("/users/{user_id}", status_code=204)
async def admin_delete_user(
    user_id: str, admin_id: str = Depends(require_admin)
):
    """管理员删除指定用户（不可删除访客或自己）。"""
    if user_id == admin_id:
        raise HTTPException(400, "不能删除自己的账号")
    if user_id == settings.guest_user_id:
        raise HTTPException(400, "不能删除访客账号")
    if not postgres.get_user(user_id):
        raise HTTPException(404, "用户不存在")
    await purge_user_data(user_id)


# ---------------- 知识库检索质量评估（逻辑在 evaluation 域） ----------------


class EvalRunIn(BaseModel):
    """运行评估请求：选择包含的案例来源。"""

    include_auto: bool = True
    include_builtin: bool = True
    custom_only: bool = False


@router.get("/eval")
def admin_eval(admin_id: str = Depends(require_admin)) -> dict:
    """返回评估配置：当前知识库文档、内置/自动/自定义案例。"""
    docs = [
        {"source": s, "name": _basename(s)} for s in _doc_sources(admin_id)
    ]
    return {
        "docs": docs,
        "builtin": _BUILTIN_CASES,
        "auto": _auto_cases(admin_id),
        "custom": _load_custom_cases(),
    }


class EvalCasesIn(BaseModel):
    """保存自定义案例请求体。"""

    cases: list[dict] = []


@router.put("/eval/custom")
def admin_save_eval_cases(
    body: EvalCasesIn, _admin_id: str = Depends(require_admin)
) -> dict:
    """保存自定义评估案例（持久化到 app_settings）。"""
    _save_custom_cases(body.cases)
    return {"custom": _load_custom_cases()}


@router.post("/eval/run")
def admin_run_eval(
    body: EvalRunIn, admin_id: str = Depends(require_admin)
) -> dict:
    """后端统一执行评估（案例生成与判定逻辑见 app.evaluation.kb_eval）。"""
    return run_kb_eval(
        admin_id,
        include_auto=body.include_auto,
        include_builtin=body.include_builtin,
        custom_only=body.custom_only,
    )
