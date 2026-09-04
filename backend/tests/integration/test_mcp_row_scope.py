"""DB MCP 行级隔离集成测试（需 Postgres）。"""
from __future__ import annotations

import uuid

import pytest

from helpers import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(), reason="需要运行中的 Postgres"
)


def test_mcp_user_scoped_tools_do_not_cross_users():
    from app.db import postgres
    from app.db.models import Document, User, gen_uuid
    from app.db.postgres import SessionLocal
    from app.mcp_integration.servers import db_query_server as server
    from app.security import hash_password

    suffix = uuid.uuid4().hex[:8]
    name_a = f"scope_a_{suffix}"
    name_b = f"scope_b_{suffix}"
    a = postgres.create_user(name_a, hash_password("pw-123456"))
    b = postgres.create_user(name_b, hash_password("pw-123456"))
    ids = [a.id, b.id]
    session_a = None
    try:
        session_a = postgres.create_session(user_id=a.id)
        postgres.add_message(session_a.id, "user", f"机密A-{suffix}")
        with SessionLocal() as db:
            db.add_all(
                [
                    Document(
                        id=gen_uuid(),
                        user_id=a.id,
                        filename=f"a_{suffix}.txt",
                        source=f"/kb/a_{suffix}.txt",
                        chunk_index=0,
                        text=f"A 文档内容 {suffix}",
                        metadata_json="{}",
                        vector_status="synced",
                    ),
                    Document(
                        id=gen_uuid(),
                        user_id=b.id,
                        filename=f"b_{suffix}.txt",
                        source=f"/kb/b_{suffix}.txt",
                        chunk_index=0,
                        text=f"B 文档内容 {suffix}",
                        metadata_json="{}",
                        vector_status="synced",
                    ),
                ]
            )
            db.commit()

        sessions_a = server.query_user_sessions(a.id)
        assert session_a.id in sessions_a
        sessions_b = server.query_user_sessions(b.id)
        assert session_a.id not in sessions_b

        msgs_a = server.query_user_messages(a.id)
        assert f"机密A-{suffix}" in msgs_a
        msgs_b = server.query_user_messages(b.id)
        assert f"机密A-{suffix}" not in msgs_b

        docs_a = server.query_user_documents(a.id)
        assert f"a_{suffix}.txt" in docs_a and f"b_{suffix}.txt" not in docs_a
        docs_b = server.query_user_documents(b.id)
        assert f"b_{suffix}.txt" in docs_b and f"a_{suffix}.txt" not in docs_b

        # 存在真实用户 → 自由 SQL 不得触碰有 user_id 的业务表
        denied = server.query_postgres("SELECT * FROM sessions")
        assert denied.startswith("拒绝执行"), denied
    finally:
        with SessionLocal() as db:
            db.query(Document).filter(Document.user_id.in_(ids)).delete(
                synchronize_session=False
            )
            for uid in ids:
                u = db.get(User, uid)
                if u:
                    db.delete(u)
            db.commit()
