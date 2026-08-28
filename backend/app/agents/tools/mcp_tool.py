"""MCP 子 Agent（工具调用）。"""
from __future__ import annotations

from functools import lru_cache

from app.agents.llm import get_llm
from app.agents.middleware import resilience_middleware
from app.agents.prompts import MCP_SYSTEM_PROMPT
from app.mcp_integration.client import get_mcp_manager


@lru_cache(maxsize=1)
def build_mcp_agent():
    """构建 MCP 子 Agent（当前已连接的所有 MCP 工具 + LLM）。缓存。"""
    from langchain.agents import create_agent

    mcp_tools = get_mcp_manager().get_langchain_tools()
    return create_agent(
        get_llm("light"),  # 子 Agent 用轻量模型
        tools=mcp_tools,
        system_prompt=MCP_SYSTEM_PROMPT,
        middleware=[resilience_middleware()],
    )
