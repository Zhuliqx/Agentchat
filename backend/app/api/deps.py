"""FastAPI 依赖：从 Authorization: Bearer 解析当前用户。

- 未携带 Authorization 头：回退为访客用户（settings.guest_user_id），
  保证现有单用户体验不被破坏；
- 携带了头但 token 无效/过期：返回 401（不静默降级访客），
  避免登录态过期后悄悄操作共享 default 命名空间。
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import settings
from app.security import decode_token


def _resolve_user_id(authorization: str | None) -> str | None:
    """从 Authorization: Bearer 头解析并校验用户 id；无效/缺失返回 None。"""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        uid = decode_token(token)
        if uid:
            return uid
    return None


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    """返回当前用户 id（无头 → 访客；带 token → 校验，无效/过期 → 401）。"""
    if not authorization:
        return settings.guest_user_id
    uid = _resolve_user_id(authorization)
    if not uid:
        raise HTTPException(401, "登录已过期或凭证无效，请重新登录")
    return uid


def require_user_id(authorization: str | None = Header(default=None)) -> str | None:
    """要求登录的依赖：无有效 token 返回 None（由路由转 401）。"""
    return _resolve_user_id(authorization)


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


def require_platform_operator(authorization: str | None = Header(default=None)) -> str:
    """平台级操作（定时任务/全局模型切换）的鉴权依赖。

    兼容默认单用户模式：库里只有内置访客用户时，匿名访客即平台操作员
    （保持零配置可用）。一旦出现任何真实注册用户，匿名请求被拒绝，
    平台级操作只允许管理员（ADMIN_USERNAMES）。
    """
    uid = _resolve_user_id(authorization)
    if uid:
        from app.db import postgres

        u = postgres.get_user(uid)
        if u and is_admin_username(u.username):
            return uid
        raise HTTPException(403, "需要管理员权限")
    from app.db import postgres

    if postgres.has_registered_users():
        raise HTTPException(401, "未登录：平台级操作需要管理员权限")
    return settings.guest_user_id


def is_platform_operator(authorization: str | None = None) -> bool:
    """只判断当前请求是否具备平台操作员能力（供前端做入口显隐）。"""
    try:
        require_platform_operator(authorization)
        return True
    except HTTPException:
        return False
