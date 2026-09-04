"""数据库查询 MCP 服务器的只读 SQL 校验单元测试。"""
from __future__ import annotations

from app.mcp_integration.servers.db_query_server import _validate_readonly


def test_allows_simple_select():
    assert _validate_readonly("SELECT * FROM sessions") is None
    assert _validate_readonly("SELECT id, title FROM sessions ORDER BY created_at DESC") is None
    assert _validate_readonly("SELECT COUNT(*) FROM documents") is None


def test_rejects_multiple_statements():
    assert _validate_readonly("SELECT 1; SELECT 2") is not None
    assert _validate_readonly("SELECT 1; DROP TABLE sessions") is not None


def test_rejects_dml():
    for sql in [
        "DELETE FROM sessions",
        "UPDATE sessions SET title='x'",
        "INSERT INTO sessions (id) VALUES ('x')",
        "DROP TABLE sessions",
        "TRUNCATE sessions",
    ]:
        assert _validate_readonly(sql) is not None, f"应拒绝: {sql}"


def test_rejects_with_cte_that_wraps_delete():
    """经典绕过：WITH CTE 里嵌 DELETE。"""
    sql = "WITH x AS (DELETE FROM sessions RETURNING *) SELECT * FROM x"
    assert _validate_readonly(sql) is not None


def test_rejects_system_catalog():
    assert _validate_readonly("SELECT * FROM pg_user") is not None
    assert _validate_readonly("SELECT * FROM pg_class") is not None


def test_rejects_empty():
    assert _validate_readonly("") is not None
    assert _validate_readonly("   ") is not None
    assert _validate_readonly(None) is not None


def test_isolation_mode_denies_user_scoped_tables():
    """多用户隔离模式下，自由 SQL 不允许碰 sessions/messages/documents。"""
    for sql in [
        "SELECT * FROM sessions",
        "SELECT * FROM messages",
        "SELECT * FROM documents",
        "SELECT s.id FROM sessions s JOIN messages m ON m.session_id = s.id",
    ]:
        assert _validate_readonly(sql, isolation=True) is not None, f"应拒绝: {sql}"


def test_isolation_mode_allows_non_scoped_tables():
    assert _validate_readonly("SELECT * FROM tasks", isolation=True) is None
    assert _validate_readonly("SELECT COUNT(*) FROM tasks", isolation=True) is None
