"""自建 MCP 服务器：数据库查询工具。

通过 stdio 被主进程拉起（FastMCP）。
提供:
- query_postgres: 在 Postgres 中执行只读 SQL
- list_tables: 列出所有表

运行方式:
    python backend/scripts/db_query_server.py

安全设计（只读）：
- 连接层强制 default_transaction_read_only=on + statement_timeout=30s（终极防护）
- sqlparse 校验：仅允许单条 SELECT、禁止 DML/DDL 关键字、禁止系统目录
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


def _validate_readonly(sql: str) -> str | None:
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
    return None


@mcp.tool()
def list_tables() -> str:
    """列出 Postgres 中当前 schema 的所有表名。"""
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
        )
        return "\n".join(r[0] for r in rows)


@mcp.tool()
def query_postgres(sql: str) -> str:
    """在 Postgres 中执行只读 SELECT 查询，返回结果集文本。

    参数 sql: 只读 SQL 语句（仅允许单条 SELECT，禁止写操作与系统目录）。
    """
    err = _validate_readonly(sql)
    if err:
        return f"拒绝执行: {err}"

    try:
        with _engine.connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = result.fetchall()[:100]
        lines = ["\t".join(cols)]
        for row in rows:
            lines.append("\t".join(str(v) for v in row))
        return "\n".join(lines) if len(lines) > 1 else "查询无结果。"
    except Exception as exc:
        return f"查询失败: {exc}"


@mcp.tool()
def get_session_stats() -> str:
    """返回对话会话与消息数量的统计。"""
    with _engine.connect() as conn:
        s = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar() or 0
        m = conn.execute(text("SELECT COUNT(*) FROM messages")).scalar() or 0
        d = conn.execute(text("SELECT COUNT(*) FROM documents")).scalar() or 0
    return f"会话数: {s}\n消息数: {m}\n文档块数: {d}"
