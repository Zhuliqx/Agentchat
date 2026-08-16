"""FastAPI 依赖：从 Authorization: Bearer 解析当前用户。

未携带 token / token 无效时回退为访客用户（settings.guest_user_id），
保证现有单用户体验不被破坏；登录用户则按 user_id 隔离会话数据。
"""
from __future__ import annotations

from fastapi import Header

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
