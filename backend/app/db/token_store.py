"""refresh token 会话存储：登记、校验、撤销（DB 为跨进程共享的事实源）。"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.postgres import engine


def _hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def store_refresh_token(user_id: str, jti: str, expires_at: datetime) -> str:
    row_id = uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO token_sessions "
                "(id, user_id, token_hash, expires_at, created_at) "
                "VALUES (:id, :uid, :h, :exp, :now)"
            ),
            {
                "id": row_id,
                "uid": user_id,
                "h": _hash_jti(jti),
                "exp": expires_at,
                "now": datetime.now(timezone.utc),
            },
        )
    return row_id


def get_refresh_token(jti: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT user_id, token_hash, expires_at, revoked_at "
                "FROM token_sessions WHERE token_hash = :h"
            ),
            {"h": _hash_jti(jti)},
        ).mappings().first()
    return dict(row) if row else None


def revoke_refresh_token(jti: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE token_sessions SET revoked_at = :now "
                "WHERE token_hash = :h AND revoked_at IS NULL"
            ),
            {"h": _hash_jti(jti), "now": datetime.now(timezone.utc)},
        )
        return result.rowcount > 0


def revoke_user_tokens(user_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE token_sessions SET revoked_at = :now "
                "WHERE user_id = :uid AND revoked_at IS NULL"
            ),
            {"uid": user_id, "now": datetime.now(timezone.utc)},
        )
