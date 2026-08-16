"""启动自建时间 MCP 服务器（stdio）。

    python scripts/time_server.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mcp_integration.servers.time_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio")
