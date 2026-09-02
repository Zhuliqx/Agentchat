"""对话历史自动压缩中间件单测（FakeLLM，不依赖真实模型/DB）。

覆盖：短历史不触发、超阈值触发并保留最近 N 条、AI/Tool 成对保留、
摘要失败不裁剪（安全模式）、图级接线端到端生效。
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.config import settings
from app.agents.history_summary import (
    build_history_summary_middleware,
)
from helpers import FakeLLM, patch_llms


def _fake_middleware(monkeypatch, text="摘要：公司成立于 2020 年"):
    """按低阈值构造中间件（FakeLLM 生成摘要）。"""
    monkeypatch.setattr(settings, "history_summary_enabled", True)
    monkeypatch.setattr(settings, "history_summary_trigger_tokens", 50)
    monkeypatch.setattr(settings, "history_summary_min_messages", 2)
    monkeypatch.setattr(settings, "history_summary_keep_messages", 4)
    monkeypatch.setattr(settings, "history_summary_max_input_tokens", 500)
    return build_history_summary_middleware(model=FakeLLM(text=text))


def _long_history(n: int = 8, chars: int = 300) -> list:
    """构造交替 Human/AI 的长历史（总 token 远超测试阈值）。"""
    out = []
    for i in range(n):
        out.append(HumanMessage(content=f"问题 {i}：" + "你" * chars))
        out.append(AIMessage(content=f"回答 {i}：" + "好" * chars))
    return out


def test_short_history_not_summarized(monkeypatch):
    """短历史（token 未超阈值）→ 不触发摘要，返回 None。"""
    mw = _fake_middleware(monkeypatch)
    state = {"messages": [HumanMessage(content="你好"), AIMessage(content="你好！")]}
    assert asyncio.run(mw.abefore_model(state, None)) is None


def test_trigger_summarizes_and_keeps_recent(monkeypatch):
    """超阈值 → 摘要 + 保留最近 N 条，旧消息被 RemoveMessage 清掉。"""
    mw = _fake_middleware(monkeypatch)
    history = _long_history()
    update = asyncio.run(mw.abefore_model({"messages": history}, None))

    assert update is not None
    msgs = update["messages"]
    # 第一条必须是 RemoveMessage(REMOVE_ALL)，随后是摘要 + 保留消息
    assert isinstance(msgs[0], RemoveMessage)
    assert msgs[0].id == REMOVE_ALL_MESSAGES
    summary, *preserved = msgs[1:]
    assert isinstance(summary, HumanMessage)
    assert summary.content.startswith("以下是本次对话的摘要：")
    assert "公司成立于 2020 年" in summary.content
    # 保留最近 4 条（与历史最后 4 条内容一致）
    assert len(preserved) == 4
    assert [m.content for m in preserved] == [m.content for m in history[-4:]]


def test_ai_tool_pairs_preserved(monkeypatch):
    """切割点不会拆散 AI 工具调用与其 ToolMessage 响应。"""
    mw = _fake_middleware(monkeypatch)
    history = []
    for i in range(5):
        history.append(HumanMessage(content=f"问题 {i}"))
        history.append(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "fake_tool", "args": {"q": i}, "id": f"call_{i}", "type": "tool_call"}
                ],
            )
        )
        history.append(ToolMessage(content=f"结果 {i}", tool_call_id=f"call_{i}"))
    update = asyncio.run(mw.abefore_model({"messages": history}, None))
    assert update is not None
    preserved = update["messages"][1 + 1 :]  # RemoveMessage + 摘要 之后
    assert preserved, "应保留最近消息"
    # 若保留区以 ToolMessage 开头说明切点拆散了 AI/Tool 对
    assert not isinstance(preserved[0], ToolMessage)
    # 保留区内每个 ToolMessage 前必有携带对应 tool_call_id 的 AIMessage
    ai_ids: set[str] = set()
    for m in preserved:
        if isinstance(m, AIMessage):
            ai_ids |= {tc.get("id") for tc in (m.tool_calls or []) if tc.get("id")}
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in ai_ids, f"孤立 ToolMessage: {m.tool_call_id}"


class _FailingSummaryLLM(FakeLLM):
    """收到摘要提示词即抛异常，模拟摘要模型故障。"""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for m in messages:
            if "你是对话摘要助手" in str(getattr(m, "content", "")):
                raise RuntimeError("summary failed")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_summary_failure_keeps_history(monkeypatch):
    """摘要 LLM 失败 → 本轮不裁剪，历史完整保留。"""
    monkeypatch.setattr(settings, "history_summary_enabled", True)
    monkeypatch.setattr(settings, "history_summary_trigger_tokens", 50)
    monkeypatch.setattr(settings, "history_summary_min_messages", 2)
    monkeypatch.setattr(settings, "history_summary_keep_messages", 4)
    monkeypatch.setattr(settings, "history_summary_max_input_tokens", 500)
    mw_fail = build_history_summary_middleware(model=_FailingSummaryLLM(text=""))

    history = _long_history()
    state = {"messages": history}
    update = asyncio.run(mw_fail.abefore_model(state, None))
    assert update is None, "摘要失败时必须跳过裁剪"
    assert state["messages"] is history
    assert len(state["messages"]) == len(history)


def test_disabled_returns_none(monkeypatch):
    """总开关关闭 → build 返回 None（图不挂载）。"""
    monkeypatch.setattr(settings, "history_summary_enabled", False)
    assert build_history_summary_middleware(model=FakeLLM(text="x")) is None


def test_graph_wiring_summarizes(monkeypatch):
    """get_supervisor_graph 挂载中间件后，长历史端到端被压缩。"""
    patch_llms(monkeypatch, supervisor=FakeLLM(text="最终回答"))
    monkeypatch.setattr(settings, "history_summary_enabled", True)
    monkeypatch.setattr(settings, "history_summary_trigger_tokens", 50)
    monkeypatch.setattr(settings, "history_summary_min_messages", 2)
    monkeypatch.setattr(settings, "history_summary_keep_messages", 4)
    monkeypatch.setattr(settings, "history_summary_max_input_tokens", 500)

    from app.agents.graph import get_supervisor_graph

    graph = get_supervisor_graph(use_rag=False, use_search=False, use_memory=False)
    history = _long_history(n=6, chars=200)
    result = asyncio.run(graph.ainvoke({"messages": history}))
    final = result["messages"]

    assert isinstance(final[0], HumanMessage)
    assert str(final[0].content).startswith("以下是本次对话的摘要：")
    # 摘要 + 保留 4 条 + 新回答，不超过 6 条
    assert len(final) <= 6
    assert isinstance(final[-1], AIMessage)
    assert "最终回答" in str(final[-1].content)
