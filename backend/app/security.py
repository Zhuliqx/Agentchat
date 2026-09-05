"""安全工具：密码哈希（stdlib pbkdf2，无额外依赖）与 JWT 签发/校验。

- 密码：PBKDF2-HMAC-SHA256，随机 16 字节盐，60w 次迭代（OWASP 建议 ≥60w）。
- JWT：HS256，payload 含 sub(user_id) / iat / exp / type；access 有效期
  settings.access_token_ttl_seconds，refresh 另有 settings.refresh_token_ttl_seconds。
  密钥来自 settings.auth_secret
  （生产环境请通过 .env 配置强随机值）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import uuid

from app.config import settings

_ITERATIONS = 600_000


def _secret() -> bytes:
    return settings.auth_secret.encode("utf-8")


# ---------------- 密码 ----------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ---------------- JWT ----------------

def create_token(
    user_id: str,
    ttl_seconds: int | None = None,
    token_type: str = "access",
    jti: str | None = None,
) -> str:
    """签发 JWT：含 sub / iat / exp / type；可选 jti（refresh 用）。"""
    import jwt

    ttl = (
        settings.access_token_ttl_seconds
        if ttl_seconds is None
        else ttl_seconds
    )
    now = time.time()
    payload = {
        "sub": user_id,
        "iat": int(now),
        "exp": int(now + max(ttl, 0)),
        "type": token_type,
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> str | None:
    """校验并解析 token，返回 user_id；签名无效返回 None。"""
    payload = decode_token_payload(token)
    if payload is None or payload.get("type") == "refresh":
        return None
    return payload.get("sub")


def decode_token_payload(token: str) -> dict | None:
    """校验并解析完整 claims（含过期）；失败返回 None。"""
    import jwt

    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"])
    except Exception:
        return None


def create_access_token(user_id: str) -> str:
    return create_token(
        user_id,
        ttl_seconds=settings.access_token_ttl_seconds,
        token_type="access",
        jti=uuid.uuid4().hex,
    )


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """签发 refresh token，返回 (token, jti)。"""
    jti = uuid.uuid4().hex
    token = create_token(
        user_id,
        ttl_seconds=settings.refresh_token_ttl_seconds,
        token_type="refresh",
        jti=jti,
    )
    return token, jti
