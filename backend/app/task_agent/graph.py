"""自主任务 Agent 的 LangGraph 图（Plan→Execute→Final 循环）。"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.db.memory_store import get_checkpointer
from app.task_agent.nodes import execute_node, final_node, plan_node
from app.task_agent.state import TaskState


def _route(state: dict) -> str:
    """子任务未执行完则继续 execute，否则跳 final。"""
    return "final" if state["current_idx"] >= len(state["plan"]) else "continue"


def build_task_agent():
    g = StateGraph(TaskState)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("final", final_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", _route, {"continue": "execute", "final": "final"})
    g.add_edge("final", END)
    # Checkpointer：长任务可中断/恢复（Time Travel 支持断点续跑）
    return g.compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=1)
def get_task_agent():
    return build_task_agent()