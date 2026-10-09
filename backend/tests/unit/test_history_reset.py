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


def test_supervisor_graph_exposes_model_node(monkeypatch):
    """守卫 reset_thread_messages 依赖的节点名。

    它用 ``as_node="model"`` 写状态；一旦 create_agent 改名，aupdate_state 会抛
    InvalidUpdateError，而截断路由把这个异常吞成 warning——表现为"截断返回 200，
    但模型仍记得被删的对话"。让改名在这里显式失败，而不是悄悄退化。
    """
    from helpers import FakeLLM, patch_llms

    from app.agents.graph import get_supervisor_graph

    patch_llms(monkeypatch, supervisor=FakeLLM(text="x"))
    graph = get_supervisor_graph(use_rag=False, use_search=False, use_memory=False)
    assert "model" in graph.get_graph().nodes, (
        "supervisor 图里没有 model 节点：reset_thread_messages 的 as_node 需要同步改名"
    )
