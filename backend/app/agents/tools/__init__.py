"""Agent 工具构建。

- rag_tool：知识库检索（RAG Agent）
- search_tool：Tavily 联网搜索
- memory_tools：长期记忆 remember/recall
- code_tool：受限沙箱代码执行
- mcp_tool：MCP 工具调用子 Agent
- confirmation：HITL 确认工具 + agent_to_tool 子 Agent 包装
- sources：检索来源记录（引用溯源）
- text：通用文本提取辅助

提示词常量集中在 ``app.agents.prompts``。
"""
from __future__ import annotations

from app.agents.prompts import CODE_SYSTEM_PROMPT, MCP_SYSTEM_PROMPT, RAG_SYSTEM_PROMPT
from app.agents.tools.code_tool import (
    _build_execute_python_tool,
    _CodeExecQuery,
    build_code_agent,
)
from app.agents.tools.confirmation import (
    _AgentQuery,
    _confirm_or_cancel,
    _ConfirmQuery,
    agent_to_tool,
    build_confirmation_tool,
)
from app.agents.tools.mcp_tool import build_mcp_agent
from app.agents.tools.memory_tools import (
    _MemoryContent,
    _NoArgs,
    build_recall_tool,
    build_remember_tool,
)
from app.agents.tools.rag_tool import (
    _build_retrieval_context,
    _build_search_knowledge_base_tool,
    _front_load,
    _RagQuery,
    build_rag_agent,
)
from app.agents.tools.search_tool import (
    _format_search_results,
    _get_tavily_search_tool,
    _make_search_arun,
    _SearchQuery,
    build_search_tool,
)
from app.agents.tools.sources import (
    _RAG_SOURCES,
    _RAG_SOURCES_LOCK,
    _record_rag_sources,
    get_recent_rag_sources,
)
from app.agents.tools.text import extract_text, last_ai_text

__all__ = [
    "RAG_SYSTEM_PROMPT",
    "MCP_SYSTEM_PROMPT",
    "CODE_SYSTEM_PROMPT",
    "build_search_tool",
    "_SearchQuery",
    "_format_search_results",
    "_get_tavily_search_tool",
    "_make_search_arun",
    "_confirm_or_cancel",
    "build_rag_agent",
    "_RagQuery",
    "_build_retrieval_context",
    "_front_load",
    "_build_search_knowledge_base_tool",
    "build_mcp_agent",
    "build_code_agent",
    "_CodeExecQuery",
    "_build_execute_python_tool",
    "build_remember_tool",
    "build_recall_tool",
    "_MemoryContent",
    "_NoArgs",
    "build_confirmation_tool",
    "_ConfirmQuery",
    "_AgentQuery",
    "agent_to_tool",
    "extract_text",
    "last_ai_text",
    "_RAG_SOURCES",
    "_RAG_SOURCES_LOCK",
    "_record_rag_sources",
    "get_recent_rag_sources",
]
