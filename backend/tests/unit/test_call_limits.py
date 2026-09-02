"""主对话图调用上限中间件单测（FakeLLM + 本地工具，不依赖真实模型/DB）。

覆盖：工具超限被拦截（continue）、模型超限直接结束（end）、两者组合
（镜像 graph.py 默认接线）、配置置 0 时不挂载中间件。
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from app.config import settings
from helpers import FakeLLM, patch_llms


def _counter_tool(calls: list[int]) -> StructuredTool:
    """本地计数工具：每次调用记录一次。"""

    async def _arun(x: int) -> str:
        calls.append(x)
        return "ok"

    return StructuredTool.from_function(
        coroutine=_arun, name="counter", description="计数工具"
    )


def _tool_call_message(i: int) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "counter",
                "args": {"x": 1},
                "id": f"call_{i}",
                "type": "tool_call",
            }
        ],
    )


class _RelentlessToolLLM(FakeLLM):
    """每次模型调用都请求工具、无视 ToolMessage（模拟失控循环）。"""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = _tool_call_message(len(messages))
        return ChatResult(generations=[ChatGeneration(message=msg)])


class _FiniteToolLLM(FakeLLM):
    """先连续请求工具，收到足够多 ToolMessage（含被拦截的错误响应）后收尾。"""

    max_tool_msgs: int = 4

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if len(tool_msgs) < self.max_tool_msgs:
            msg = _tool_call_message(len(messages))
        else:
            msg = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_tool_limit_blocks_excess_calls(monkeypatch):
    """工具超限（continue）→ 超限调用被拦截不执行，模型正常收尾。"""
    calls: list[int] = []
    agent = create_agent(
        _FiniteToolLLM(max_tool_msgs=4),
        tools=[_counter_tool(calls)],
        middleware=[ToolCallLimitMiddleware(run_limit=3, exit_behavior="continue")],
    )
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("go")]}))
    assert len(calls) == 3, "第 4 次工具调用应被拦截，实际执行次数" + str(len(calls))
    assert any(isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"])
    assert str(result["messages"][-1].content) == "done"


def test_model_limit_ends_runaway_loop():
    """模型超限（end）→ 无条件结束并注入说明消息，不再继续调用。"""
    calls: list[int] = []
    agent = create_agent(
        _RelentlessToolLLM(),
        tools=[_counter_tool(calls)],
        middleware=[ModelCallLimitMiddleware(run_limit=5, exit_behavior="end")],
    )
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("go")]}))
    assert len(calls) <= 5
    assert any("Model call limits exceeded" in str(m.content) for m in result["messages"])


def test_combined_limits_mirror_production():
    """工具 3 次 + 模型 8 次（镜像 graph.py 默认组合）→ 执行恰好 3 次后结束。"""
    calls: list[int] = []
    # 具体泛型中间件混排需放宽为 list[Any]：create_agent 的 AgentMiddleware
    # StateT/ContextT 是固定类型参数，具体泛型联合无法直接赋值。
    middleware: list[Any] = [
        ToolCallLimitMiddleware(run_limit=3, exit_behavior="continue"),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
    ]
    agent = create_agent(
        _RelentlessToolLLM(),
        tools=[_counter_tool(calls)],
        middleware=middleware,
    )
    result = asyncio.run(agent.ainvoke({"messages": [HumanMessage("go")]}))
    assert len(calls) == 3, "工具调用应恰好执行到上限"
    assert any("Model call limits exceeded" in str(m.content) for m in result["messages"])


def test_zero_limit_disables_middleware(monkeypatch):
    """配置置 0 → 不挂载上限中间件，图照常可用。"""
    monkeypatch.setattr(settings, "agent_max_tool_calls", 0)
    monkeypatch.setattr(settings, "agent_max_model_calls", 0)
    patch_llms(monkeypatch, supervisor=FakeLLM(text="ok"))

    from app.agents.graph import get_supervisor_graph

    graph = get_supervisor_graph(use_rag=False, use_search=False, use_memory=False)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage("hi")]}))
    assert str(result["messages"][-1].content) == "ok"
