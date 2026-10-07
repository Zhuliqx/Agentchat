"""截断历史后重置图状态：显示消息 → LangChain 消息转换（纯函数，无外部依赖）。"""
from __future__ import annotations

from app.agents.graph import display_messages_to_lc


def test_display_messages_to_lc_keeps_order_and_roles():
    msgs = display_messages_to_lc([("user", "第一问"), ("assistant", "第一答")])
    assert [type(m).__name__ for m in msgs] == ["HumanMessage", "AIMessage"]
    assert [m.content for m in msgs] == ["第一问", "第一答"]


def test_display_messages_to_lc_skips_unknown_role_and_blank():
    msgs = display_messages_to_lc([("user", "a"), ("system", "x"), ("assistant", "   ")])
    assert [m.content for m in msgs] == ["a"]


def test_reset_thread_messages_returns_false_without_checkpointer(monkeypatch):
    """无 Checkpointer（未起 Docker）时只跳过重置，不抛异常——接口要保持 200。"""
    import asyncio

    from app.agents import graph

    monkeypatch.setattr(graph, "get_checkpointer", lambda: None)
    assert asyncio.run(graph.reset_thread_messages("sid", [("user", "x")])) is False
