"""端到端冒烟测试：验证健康检查与三类 Agent 问答。

用法:
    python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://localhost:8000"


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    print("==> 1. 健康检查")
    h = get("/api/health")
    print("   ", h.get("status"), "| MCP:", h.get("mcp_servers"), "| Milvus entities:", h.get("milvus", {}).get("num_entities"))

    print("==> 2. RAG 知识库问答")
    r = post("/api/chat", {"message": "示例科技公司有多少名员工？", "use_rag": True, "use_search": False})
    print("    用到的 Agent:", r["used_agents"])
    print("    回答:", r["answer"][:120])

    print("==> 3. MCP 数据库查询")
    r = post("/api/chat", {"message": "帮我统计一下数据库里有多少个会话", "use_rag": False, "use_search": False})
    print("    用到的 Agent:", r["used_agents"])
    print("    回答:", r["answer"][:120])

    print("==> 4. 联网搜索（Tavily）")
    r = post("/api/chat", {"message": "搜索一下今天的最新AI新闻", "use_rag": False, "use_search": True})
    print("    用到的 Agent:", r["used_agents"])
    print("    回答:", r["answer"][:120])

    print("\n冒烟测试完成 ✅")


if __name__ == "__main__":
    main()
