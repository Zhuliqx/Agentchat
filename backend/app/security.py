"""安全工具：密码哈希（stdlib pbkdf2，无额外依赖）与 JWT 签发/校验。

- 密码：PBKDF2-HMAC-SHA256，随机 16 字节盐，60w 次迭代（OWASP 建议 ≥60w）。
- JWT：HS256，payload 含 sub(user_id) / iat；未签发 exp（token 不设有效期）；
  密钥来自 settings.auth_secret（生产环境请通过 .env 配置强随机值）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

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

def create_token(user_id: str) -> str:
    import jwt

    payload = {"sub": user_id, "iat": int(time.time())}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_token(token: str) -> str | None:
    """校验并解析 token，返回 user_id；签名无效返回 None。"""
    import jwt

    try:
        payload = jwt.decode(token, _secret(), algorithms=["HS256"])
        return payload.get("sub")
    except Exception:
        return None
