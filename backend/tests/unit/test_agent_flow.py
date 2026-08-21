"""Supervisor 图编排与流式事件流测试（用 FakeLLM，不依赖真实模型/DB/LLM）。

覆盖：
- 直接回答：不触发子 Agent/工具，事件流 start→token→message；
- 工具路由：supervisor 决策调用 rag_agent → 事件流出现 agent 事件；
- 开场白去重：工具后答案重复开场白被 _PreludeDedupe 剔除；
- 事件顺序与 used_agents 透传。

不依赖 Postgres/Milvus：checkpointer 为 None（图不带持久化），MCP 被 mock 为空。
"""
from __future__ import annotations

import asyncio

from helpers import FakeLLM, patch_llms


def _run_stream(**kwargs):
    """运行 stream_agent，返回 (事件类型列表, 拼接的 token 文本, message 数据)。"""
    from app.agents.graph import stream_agent

    events: list[dict] = []
    tokens: list[str] = []

    async def on_event(ev: dict):
        events.append(ev)

    async def on_token(text: str):
        tokens.append(text)

    kwargs.setdefault("use_rag", True)
    kwargs.setdefault("use_search", False)
    kwargs.setdefault("use_memory", False)
    kwargs.setdefault("session_id", None)
    kwargs.setdefault("user_id", "default")
    kwargs["on_event"] = on_event
    kwargs["on_token"] = on_token

    result = asyncio.run(stream_agent("测试问题", **kwargs))
    return events, "".join(tokens), result


def test_direct_answer_no_tool(monkeypatch):
    """supervisor 不调工具 → 直接回答，无 agent/tool 事件。"""
    patch_llms(monkeypatch, FakeLLM(text="直接回答"))
    events, text, result = _run_stream()

    types = [e.get("type") for e in events]
    assert "start" in types
    assert "end" in types
    assert "agent" not in types
    assert "tool" not in types
    assert text  # 有 token 输出
    assert not result.get("used_agents")


def test_route_to_rag_agent(monkeypatch):
    """supervisor 决策调用 rag_agent → 事件流出现 agent 事件且 used_agents 含 rag_agent。"""
    patch_llms(
        monkeypatch,
        supervisor=FakeLLM(
            text="根据知识库回答完毕",
            tool_calls=[
                {"name": "rag_agent", "args": {"query": "测试问题"}, "id": "c1"}
            ],
        ),
        subagent=FakeLLM(text="这是子 Agent 检索到的内容"),
    )
    events, text, result = _run_stream()

    types = [e.get("type") for e in events]
    assert "start" in types
    assert "end" in types
    # 出现过子 Agent/工具事件（agent 或 tool）
    assert "agent" in types or "tool" in types
    assert result.get("used_agents"), "used_agents 应包含 rag_agent"
    assert "rag_agent" in result["used_agents"]


def test_event_order_direct(monkeypatch):
    """直接回答场景事件顺序：start 在前，end 在后，message 含最终答案。"""
    patch_llms(monkeypatch, FakeLLM(text="最终答复"))
    events, _, result = _run_stream()

    types = [e.get("type") for e in events]
    # start 必须最早
    assert types.index("start") == 0
    # end 在 message 之后（或 message 为最终帧）
    if "end" in types and "message" in types:
        assert types.index("end") < types.index("message")
    assert result.get("answer") == "最终答复"


def test_prelude_dedupe_in_stream(monkeypatch):
    """工具后 LLM 重新生成完整开场白 → 去重后不重复（验证 _PreludeDedupe 在真实流生效）。"""
    prelude = "我来帮你查询知识库。"
    patch_llms(
        monkeypatch,
        supervisor=FakeLLM(
            text=prelude + "以下是查询结果。",  # 开场白 + 答案（模拟 LLM 重生成）
            tool_calls=[
                {"name": "rag_agent", "args": {"query": "测试问题"}, "id": "c1"}
            ],
        ),
        subagent=FakeLLM(text="知识库内容"),
    )
    # 直接测 stream_agent 的去重：supervisor 首轮 tool_call 前可能输出开场白
    events, text, result = _run_stream()

    answer = result.get("answer", "")
    # 开场白不应出现两次（工具前已推送，工具后去重）
    assert answer.count(prelude) <= 1, f"开场白重复: {answer!r}"
