"""自主任务 Agent 的 LangGraph 图：一期 fixed(Plan→Execute→Final) 与二期 replan(每步重规划+独立 check)。"""
from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.db.memory_store import get_checkpointer
from app.task_agent.nodes import (
    check_node,
    execute_action_node,
    execute_node,
    final_node,
    plan_node,
    replan_node,
)
from app.task_agent.state import TaskState


def _route_fixed(state: dict) -> str:
    """fixed：子任务未执行完则继续 execute，否则跳 final。"""
    return "final" if state["current_idx"] >= len(state["plan"]) else "continue"


def _route_replan(state: dict) -> str:
    """check 之后：done 则 final，否则回 replan 重新规划下一步。"""
    return "final" if state.get("done") else "replan"


def _route_after_replan(state: dict) -> str:
    """replan 之后：空动作(或已判 done)则直接结束，否则执行该动作。"""
    return "final" if state.get("done") or not state.get("current_action") else "execute"


def build_fixed_agent():
    g = StateGraph(TaskState)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.add_node("final", final_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", _route_fixed, {"continue": "execute", "final": "final"})
    g.add_edge("final", END)
    return g.compile(checkpointer=get_checkpointer())


def build_replan_agent():
    g = StateGraph(TaskState)
    g.add_node("replan", replan_node)
    g.add_node("execute", execute_action_node)
    g.add_node("check", check_node)
    g.add_node("final", final_node)
    g.add_edge(START, "replan")
    g.add_conditional_edges("replan", _route_after_replan, {"final": "final", "execute": "execute"})
    g.add_edge("execute", "check")
    g.add_conditional_edges("check", _route_replan, {"final": "final", "replan": "replan"})
    g.add_edge("final", END)
    return g.compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=2)
def get_task_agent():
    """按 TASK_AGENT_MODE 返回对应图(fixed 一期 / replan 二期默认)。"""
    if settings.task_agent_mode.lower() == "fixed":
        return build_fixed_agent()
    return build_replan_agent()