"""自主任务 Agent 的 LangGraph 图：一期 fixed(Plan→Execute→Final) 与二期 replan(每步重规划+独立 check)。"""
from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

from app.config import settings
from app.db.memory_store import get_checkpointer
from app.task_agent.nodes import (
    _is_failed_finding,
    check_node,
    execute_action_node,
    execute_node,
    final_node,
    human_confirm_node,
    plan_node,
    replan_node,
    verify_node,
)
from app.task_agent.state import TaskState

logger = logging.getLogger(__name__)


def _route_fixed(state: dict) -> str:
    """fixed：子任务未执行完则继续 execute，否则跳 final。"""
    return "final" if state["current_idx"] >= len(state["plan"]) else "continue"


def _route_replan(state: dict) -> str:
    """check 之后：done 则 final，否则回 replan 重新规划下一步。"""
    return "final" if state.get("done") else "replan"


def _route_after_replan(state: dict) -> str:
    """replan 之后：空动作(或已判 done)则直接结束，否则交 HITL 确认(关闭时透传执行)。"""
    return "final" if state.get("done") or not state.get("current_action") else "confirm"


def _route_after_confirm(state: dict) -> str:
    """HITL 确认之后：skip 则进 check(重新判断/重规划)，否则执行该动作。"""
    return "check" if (state.get("_confirm_verb") or "proceed") == "skip" else "execute"


def _route_after_execute(state: dict) -> str:
    """执行之后：失败且未到重试上限 → 自检 verify；否则直接进 check。"""
    findings = state.get("findings") or []
    last = findings[-1] if findings else ""
    retries = int(state.get("retries") or 0)
    if _is_failed_finding(last) and retries < settings.task_agent_max_retries:
        return "verify"
    return "check"


def _route_after_verify(state: dict) -> str:
    """自检之后：判定值得重试 → 回执行；否则进 check。"""
    return "execute" if state.get("should_retry") else "check"


# ---------------- 节点级 retry + timeout + error_handler ----------------
# LLM 节点失败 → retry_policy 重试(瞬时错误) → 耗尽后 error_handler 降级(返回 Command 才能续跑)。
# execute/execute_action 是"业务子任务"(失败标记 finding)，保留节点内 try/except，不参与重试。

_LLM_RETRY = RetryPolicy(max_attempts=2)  # 瞬时错误重试(默认 retry_on；网络重试由 get_llm 客户端已兜底)
_LLM_TIMEOUT = settings.llm_timeout  # 单节点 LLM 调用超时(秒)


def _err_plan(state: dict, error: NodeError) -> Command:
    """plan LLM 失败 → 回退单一子任务(直接回答目标)，继续 execute。"""
    return Command(
        update={"plan": [{"id": "1", "desc": "请直接回答：" + state["goal"], "status": "pending", "result": ""}],
                "current_idx": 0, "findings": []},
        goto="execute",
    )


def _err_replan(state: dict, error: NodeError) -> Command:
    """replan LLM 失败 → 视为可完成(安全收敛)，进 final 整合。"""
    return Command(update={"current_action": "", "done": True, "retries": 0}, goto="final")


def _err_check(state: dict, error: NodeError) -> Command:
    """check LLM 失败 → 保守判完成，进 final。"""
    return Command(update={"done": True}, goto="final")


def _err_final(state: dict, error: NodeError) -> Command:
    """final LLM 失败 → 兜底交付，结束。"""
    return Command(update={"final_answer": "任务已执行，但结果整合失败。"}, goto=END)


def _err_verify(state: dict, error: NodeError) -> Command:
    """verify LLM 失败 → 放弃重试、进 check。"""
    return Command(update={"should_retry": False, "retries": int(state.get("retries") or 0)}, goto="check")


def build_fixed_agent():
    g = StateGraph(TaskState)
    g.add_node("plan", plan_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_plan)
    g.add_node("execute", execute_node)
    g.add_node("final", final_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_final)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", _route_fixed, {"continue": "execute", "final": "final"})
    g.add_edge("final", END)
    return g.compile(checkpointer=get_checkpointer())


def build_replan_agent():
    g = StateGraph(TaskState)
    g.add_node("replan", replan_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_replan)
    g.add_node("confirm", human_confirm_node)  # 节点级 HITL(关闭时透传)
    g.add_node("execute", execute_action_node)
    g.add_node("verify", verify_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_verify)
    g.add_node("check", check_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_check)
    g.add_node("final", final_node, retry_policy=_LLM_RETRY, timeout=_LLM_TIMEOUT, error_handler=_err_final)
    g.add_edge(START, "replan")
    g.add_conditional_edges("replan", _route_after_replan, {"final": "final", "confirm": "confirm"})
    g.add_conditional_edges("confirm", _route_after_confirm, {"execute": "execute", "check": "check"})
    g.add_conditional_edges("execute", _route_after_execute, {"verify": "verify", "check": "check"})
    g.add_conditional_edges("verify", _route_after_verify, {"execute": "execute", "check": "check"})
    g.add_conditional_edges("check", _route_replan, {"final": "final", "replan": "replan"})
    g.add_edge("final", END)
    return g.compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=2)
def get_task_agent():
    """按 TASK_AGENT_MODE 返回对应图(fixed 一期 / replan 二期默认)。"""
    if settings.task_agent_mode.lower() == "fixed":
        return build_fixed_agent()
    return build_replan_agent()


async def list_task_history(session_id: str, limit: int = 30) -> list[dict]:
    """Time Travel：列出自主任务线程的 checkpoint 历史(新→旧)，供时间线/回退/分叉。

    每条含 checkpoint_id(可用于 replay / fork)、parent_checkpoint_id、创建时间、
    next(待执行节点)、摘要(final_answer 或最后一条 finding)、以及是否处于中断等待确认。
    需要 Postgres checkpointer(未连库返回空)。
    """
    cp = get_checkpointer()
    if cp is None or not session_id:
        return []
    graph = get_task_agent()
    result: list[dict] = []
    try:
        async for snap in graph.aget_state_history({"configurable": {"thread_id": session_id}}):
            cfg = (snap.config or {}).get("configurable", {}) or {}
            parent_cfg = (snap.parent_config or {}).get("configurable", {}) or {}
            values = snap.values or {}
            summary = values.get("final_answer") or _last_finding(values.get("findings"))
            created = getattr(snap, "created_at", None)
            iso = created.isoformat() if created is not None and hasattr(created, "isoformat") else None
            result.append(
                {
                    "checkpoint_id": cfg.get("checkpoint_id"),
                    "checkpoint_ns": cfg.get("checkpoint_ns") or "",
                    "parent_checkpoint_id": parent_cfg.get("checkpoint_id"),
                    "created_at": iso,
                    "next": list(snap.next) if snap.next else [],
                    "summary": (summary or "")[:150],
                    "task_count": len(getattr(snap, "tasks", []) or []),
                    "interrupted": any(
                        getattr(t, "interrupts", None)
                        for t in (getattr(snap, "tasks", []) or [])
                    ),
                }
            )
            if len(result) >= limit:
                break
    except Exception as exc:  # noqa: BLE001 - 读取失败安全返回空
        logger.warning("读取自主任务 checkpoint 历史失败: %s", exc)
        return []
    return result


def _last_finding(findings: object) -> str:
    """取 findings 最后一条作为摘要(供历史列表展示)。"""
    items = findings if isinstance(findings, list) else []
    return items[-1] if items and isinstance(items[-1], str) else ""