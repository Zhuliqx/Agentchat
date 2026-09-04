"""自建 MCP 服务器：数据库查询工具（多用户行级隔离）。

通过 stdio 被主进程拉起（FastMCP）。

安全设计：
- 连接层强制 default_transaction_read_only=on + statement_timeout=30s（终极防护）；
- ``query_postgres`` 自由 SQL 只读校验：仅允许单条 SELECT、禁止 DML/DDL、
  系统目录与凭据表；实例存在真实注册用户（isolation 模式）时，再禁止
  sessions/messages/documents 等带 user_id 的业务表（这些数据只能走行级工具）；
- ``query_user_*`` / ``get_session_stats`` 行级工具：接收 user_id，宿主包装层
  会强制注入当前登录用户（LLM 不能指定他人），服务端 SQL 固定按 user_id 过滤。

运行方式:
    python backend/scripts/db_query_server.py
"""
from __future__ import annotations

import re

import sqlparse
from mcp.server.fastmcp import FastMCP

from app.config import settings

mcp = FastMCP("db-query")

# 复用项目里的 SQLAlchemy 引擎（强制只读 + 语句超时 30s）
from sqlalchemy import create_engine, text

_engine = create_engine(
    settings.postgres_dsn,
    pool_pre_ping=True,
    connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=30000"
    },
)

# 危险 DML/DDL 关键字（防止 WITH CTE 等绕过）
_FORBIDDEN = [
    "delete", "update", "insert", "drop", "alter", "create",
    "truncate", "grant", "revoke", "merge", "vacuum",
]
# 表级边界：认证凭据 / 运行时配置 / LangGraph 内部状态不允许经 LLM 查询
_DENIED_RELATIONS = (
    "users",
    "app_settings",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "store",
    "store_writes",
)
_DENIED_COLUMNS = ("password_hash",)
# 带 user_id 的用户数据表：isolation 模式下自由 SQL 一律拒绝，只能走行级工具
_SCOPED_RELATIONS = ("sessions", "messages", "documents")


def _validate_readonly(sql: str | None, isolation: bool = False) -> str | None:
    """校验 SQL 是否为安全的只读查询。返回错误信息或 None。"""
    if not sql or not sql.strip():
        return "SQL 为空。"
    if ";" in sql:
        return "不允许多语句（含分号）。"

    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        return "仅允许单条语句。"

    stmt_type = (parsed[0].get_type() or "").upper()
    if stmt_type not in ("SELECT", "UNKNOWN"):
        return f"仅允许只读 SELECT 查询（当前: {stmt_type or '无法识别'}）。"

    lower = sql.lower()
    for kw in _FORBIDDEN:
        if re.search(rf"\b{kw}\b", lower):
            return f"检测到被禁止的操作: {kw}"
    if re.search(r"\bpg_", lower):
        return "禁止访问系统目录（pg_*）。"
    for rel in _DENIED_RELATIONS:
        if re.search(rf"\b{rel}\b", lower):
            return f"禁止访问敏感表: {rel}"
    for col in _DENIED_COLUMNS:
        if re.search(rf"\b{col}\b", lower):
            return f"禁止访问敏感字段: {col}"
    if isolation:
        for rel in _SCOPED_RELATIONS:
            if re.search(rf"\b{rel}\b", lower):
                return (
                    f"多用户模式下禁止用自由 SQL 查询 {rel}，"
                    "请使用 query_user_* 行级工具"
                )
    return None


def _isolation_mode() -> bool:
    """实例是否处于多用户模式（存在除内置访客外的注册用户）。"""
    try:
        with _engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM users WHERE id <> :gid"),
                {"gid": settings.guest_user_id},
            ).scalar()
        return bool(n and int(n) > 0)
    except Exception:
        return False  # users 表不可读时按单用户兼容处理


def _render_table(cols: list[str], rows: list[tuple]) -> str:
    """把查询结果渲染为制表符文本（LLM 易读）。"""
    if not rows:
        return ""
    lines = ["\t".join(str(c) for c in cols)]
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


@mcp.tool()
def list_tables() -> str:
    """列出 Postgres 中当前 schema 的表名（多用户模式下过滤敏感表）。"""
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
        ).all()
    names = [r[0] for r in rows]
    if _isolation_mode():
        names = [n for n in names if n not in _DENIED_RELATIONS and n not in _SCOPED_RELATIONS]
    return "\n".join(names)


@mcp.tool()
def query_postgres(sql: str) -> str:
    """执行只读 SELECT（多用户模式下禁止查询 sessions/messages/documents，改用 query_user_*）。"""
    err = _validate_readonly(sql, isolation=_isolation_mode())
    if err:
        return f"拒绝执行: {err}"

    try:
        with _engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = result.fetchall()[:100]
        return _render_table(cols, rows) or "查询无结果。"
    except Exception as exc:
        return f"查询失败: {exc}"


def _require_user_id(user_id: str) -> str | None:
    if _isolation_mode() and not user_id:
        return "多用户模式：user_id 必须由宿主注入，不能为空。"
    return None


@mcp.tool()
def query_user_sessions(user_id: str = "", limit: int = 20) -> str:
    """查询当前用户自己的会话列表（id/title/pinned/时间）。"""
    denied = _require_user_id(user_id)
    if denied:
        return denied
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, title, pinned, created_at, updated_at "
                "FROM sessions WHERE user_id = :uid "
                "ORDER BY updated_at DESC LIMIT :lim"
            ),
            {"uid": user_id, "lim": max(1, min(int(limit or 20), 100))},
        ).all()
    return _render_table(["id", "title", "pinned", "created_at", "updated_at"], rows) or "该用户没有会话。"


@mcp.tool()
def query_user_messages(
    user_id: str = "", session_id: str = "", keyword: str = "", limit: int = 20
) -> str:
    """查询当前用户会话中的消息（可按会话/关键词过滤；JOIN 强制校验归属）。"""
    denied = _require_user_id(user_id)
    if denied:
        return denied
    where = "s.user_id = :uid"
    params: dict = {"uid": user_id, "lim": max(1, min(int(limit or 20), 100))}
    if session_id:
        where += " AND m.session_id = :sid"
        params["sid"] = session_id
    if keyword:
        where += " AND m.content ILIKE :kw"
        params["kw"] = f"%{keyword}%"
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.id, m.role, LEFT(m.content, 500) AS content, m.created_at "
                "FROM messages m JOIN sessions s ON s.id = m.session_id "
                f"WHERE {where} ORDER BY m.created_at DESC LIMIT :lim"
            ),
            params,
        ).all()
    return _render_table(["id", "role", "content", "created_at"], rows) or "该用户没有匹配的消息。"


@mcp.tool()
def query_user_documents(
    user_id: str = "", keyword: str = "", limit: int = 20
) -> str:
    """查询当前用户知识库的文档块（可按关键词过滤）。"""
    denied = _require_user_id(user_id)
    if denied:
        return denied
    where = "user_id = :uid"
    params: dict = {"uid": user_id, "lim": max(1, min(int(limit or 20), 100))}
    if keyword:
        where += " AND text ILIKE :kw"
        params["kw"] = f"%{keyword}%"
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT filename, source, chunk_index, LEFT(text, 300) AS text "
                "FROM documents "
                f"WHERE {where} ORDER BY created_at DESC LIMIT :lim"
            ),
            params,
        ).all()
    return _render_table(["filename", "source", "chunk_index", "text"], rows) or "该用户没有匹配的文档块。"


@mcp.tool()
def get_session_stats(user_id: str = "") -> str:
    """统计当前用户（宿主注入）的会话/消息/文档数；单用户模式返回全库统计。"""
    denied = _require_user_id(user_id)
    if denied:
        return denied
    with _engine.connect() as conn:
        if _isolation_mode():
            s = conn.execute(
                text("SELECT COUNT(*) FROM sessions WHERE user_id = :uid"), {"uid": user_id}
            ).scalar() or 0
            m = conn.execute(
                text(
                    "SELECT COUNT(*) FROM messages m "
                    "JOIN sessions s ON s.id = m.session_id WHERE s.user_id = :uid"
                ),
                {"uid": user_id},
            ).scalar() or 0
            d = conn.execute(
                text("SELECT COUNT(*) FROM documents WHERE user_id = :uid"), {"uid": user_id}
            ).scalar() or 0
        else:
            s = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar() or 0
            m = conn.execute(text("SELECT COUNT(*) FROM messages")).scalar() or 0
            d = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar() or 0
    return f"会话数: {s}\n消息数: {m}\n文档块数: {d}"
