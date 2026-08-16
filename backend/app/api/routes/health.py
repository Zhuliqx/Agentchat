"""健康检查接口。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.db.postgres import engine
from app.mcp_integration.client import get_mcp_manager
from app.rag.vector_store import stats as milvus_stats

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """检查各组件连接状态。"""
    # Postgres
    pg_ok = True
    pg_error = ""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pg_ok = False
        pg_error = str(exc)

    # Milvus
    ms = milvus_stats()

    # MCP 服务器
    mcp = get_mcp_manager()
    mcp_names = mcp.server_names()

    return {
        "status": "ok" if pg_ok and ms.get("connected") else "degraded",
        "postgres": {"ok": pg_ok, "error": pg_error},
        "milvus": ms,
        "mcp_servers": mcp_names,
    }
