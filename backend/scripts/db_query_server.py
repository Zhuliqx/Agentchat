"""启动自建数据库查询 MCP 服务器（stdio）。

被主进程作为子进程拉起，也可单独运行测试:
    python scripts/db_query_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mcp_integration.servers.db_query_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio")
