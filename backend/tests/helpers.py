"""测试辅助：FakeLLM 与编排测试的 monkeypatch 工具。

FakeLLM 按"消息历史最后一条类型"决策：
- 最后一条是 ToolMessage → 返回文本（工具执行后的最终回答）；
- 配置了 tool_calls → 返回工具调用（触发 supervisor 路由）；
- 否则 → 返回文本（直接回答）。

用于驱动 supervisor 图（create_agent）而不依赖真实 DeepSeek。
"""
from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class FakeLLM(BaseChatModel):
    """按消息历史返回预设响应的假 LLM。"""

    text: str = "最终回答"
    tool_calls: list[dict] = []  # 空 = 不调工具（直接回答）

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs: Any):
        """Fake 模型不真正绑定工具 schema；返回自身即可（create_agent 会调用）。"""
        return self

    def _generate(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> ChatResult:
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            msg = AIMessage(content=self.text)
        elif self.tool_calls:
            msg = AIMessage(content="", tool_calls=self.tool_calls)
        else:
            msg = AIMessage(content=self.text)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(
        self, messages, stop=None, run_manager=None, **kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        result = self._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )
        msg = result.generations[0].message
        if msg.tool_calls:
            chunks = [
                {
                    "name": tc.get("name", ""),
                    "args": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    "id": tc.get("id", "call_fake"),
                    "index": i,
                }
                for i, tc in enumerate(msg.tool_calls)
            ]
            chunk = AIMessageChunk(content="", tool_call_chunks=chunks)
        else:
            chunk = AIMessageChunk(content=msg.content)
        yield ChatGenerationChunk(message=chunk)


class _FakeMcpManager:
    """空 MCP manager：避免 build_mcp_agent 连真实 MCP。"""

    def get_langchain_tools(self) -> list[Any]:
        return []


def patch_llms(
    monkeypatch: Any,
    supervisor: FakeLLM,
    subagent: Optional[FakeLLM] = None,
) -> None:
    """注入 FakeLLM 并隔离 MCP，清空图缓存。"""
    from app.agents import graph as graph_mod
    from app.agents import tools as tools_mod

    sub = subagent or supervisor
    monkeypatch.setattr(graph_mod, "get_llm", lambda kind="main": supervisor)
    monkeypatch.setattr(tools_mod, "get_llm", lambda kind="light": sub)
    monkeypatch.setattr(tools_mod, "get_mcp_manager", lambda: _FakeMcpManager())
    # 关闭图执行缓存，避免 FakeLLM 相同输入被缓存跳过
    from app.config import settings

    monkeypatch.setattr(settings, "agent_cache_enabled", False)
    graph_mod.clear_graph_cache()
