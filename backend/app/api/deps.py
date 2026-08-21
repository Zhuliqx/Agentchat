"""FastAPI 依赖：从 Authorization: Bearer 解析当前用户。

未携带 token / token 无效时回退为访客用户（settings.guest_user_id），
保证现有单用户体验不被破坏；登录用户则按 user_id 隔离会话数据。
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import settings
from app.security import decode_token


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    """返回当前用户 id（带 token → 校验；否则 → 访客）。"""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        uid = decode_token(token)
        if uid:
            return uid
    return settings.guest_user_id


def require_user_id(authorization: str | None = Header(default=None)) -> str | None:
    """要求登录的依赖：无有效 token 返回 None（由路由转 401）。"""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        uid = decode_token(token)
        if uid:
            return uid
    return None


def admin_usernames() -> set[str]:
    """当前配置的管理员用户名集合（逗号分隔）。"""
    return {x.strip() for x in settings.admin_usernames.split(",") if x.strip()}


def is_admin_username(username: str) -> bool:
    """用户名是否为管理员。"""
    return username in admin_usernames()


def require_admin(authorization: str | None = Header(default=None)) -> str:
    """要求管理员登录的依赖：未登录 401，非管理员 403。"""
    uid = require_user_id(authorization)
    if not uid:
        raise HTTPException(401, "未登录")
    from app.db import postgres

    u = postgres.get_user(uid)
    if not u or not is_admin_username(u.username):
        raise HTTPException(403, "需要管理员权限")
    return uid
