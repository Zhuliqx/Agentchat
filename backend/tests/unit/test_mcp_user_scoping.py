"""宿主 MCP 包装层强制注入当前用户的单元测试。"""
from __future__ import annotations

import asyncio

from mcp.types import Tool

from app.agents.context import current_user_context, current_user_id
from app.mcp_integration.client import McpClientManager


def _db_tool(name: str, props: dict, required: list[str]) -> Tool:
    return Tool(
        name=name,
        description="test",
        inputSchema={"type": "object", "properties": props, "required": required},
    )


def test_current_user_context_restores_previous():
    async def go():
        assert current_user_id() == "default"
        async with current_user_context("alice"):
            assert current_user_id() == "alice"
        assert current_user_id() == "default"

    asyncio.run(go())
    assert current_user_id() == "default"


def test_db_tool_user_id_is_overridden_from_context():
    """LLM 即使传 bob，包装层也必须强制改成当前用户 alice。"""
    captured: dict = {}

    class _Session:
        async def call_tool(self, name: str, arguments: dict):
            captured["name"] = name
            captured["arguments"] = arguments
            return type("R", (), {"content": []})()

    tool = _db_tool(
        "query_user_sessions",
        {"user_id": {"type": "string"}, "limit": {"type": "integer"}},
        [],
    )
    wrapped = McpClientManager._to_langchain_tool("db", tool, _Session())

    async def go():
        async with current_user_context("alice"):
            await wrapped.ainvoke({"limit": 5})

    asyncio.run(go())
    assert captured["arguments"]["user_id"] == "alice"
    assert captured["arguments"]["limit"] == 5


def test_non_db_tool_does_not_inject_user_id():
    captured: dict = {}

    class _Session:
        async def call_tool(self, name: str, arguments: dict):
            captured["arguments"] = arguments
            return type("R", (), {"content": []})()

    tool = _db_tool("get_current_time", {"timezone_name": {"type": "string"}}, [])
    wrapped = McpClientManager._to_langchain_tool("time", tool, _Session())

    async def go():
        async with current_user_context("alice"):
            await wrapped.ainvoke({"timezone_name": "UTC"})

    asyncio.run(go())
    assert captured["arguments"] == {"timezone_name": "UTC"}
