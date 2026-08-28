"""自主任务 Agent 的 LangGraph 图（fixed 一期 / replan 二期，依赖注入）。"""
from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.errors import NodeError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

from task_agent.config import TaskAgentConfig
from task_agent.executor import DefaultExecutor, Executor
from task_agent.llm import LLMFactory
from task_agent.nodes import Runtime, _is_failed_finding, make_nodes
from task_agent.state import TaskState

logger = logging.getLogger(__name__)


# ---------------- 条件路由 ----------------


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


def _route_after_execute(config: TaskAgentConfig = TaskAgentConfig()):
    """执行之后：失败且未到重试上限 → 自检 verify；否则直接进 check。"""

    def route(state: dict) -> str:
        findings = state.get("findings") or []
        last = findings[-1] if findings else ""
        retries = int(state.get("retries") or 0)
        if _is_failed_finding(last) and retries < config.max_retries:
            return "verify"
        return "check"

    return route


def _route_after_verify(state: dict) -> str:
    """自检之后：判定值得重试 → 回执行；否则进 check。"""
    return "execute" if state.get("should_retry") else "check"


# ---------------- 节点级 retry + timeout + error_handler ----------------
# LLM 节点失败 → retry_policy 重试(瞬时错误) → 耗尽后 error_handler 降级(返回 Command 才能续跑)。
# execute/execute_action 是"业务子任务"(失败标记 finding)，保留节点内 try/except，不参与重试。


def _is_transient(exc: BaseException) -> bool:
    """判断 LLM 节点失败是否为"瞬时错误"(值得重试)。

    官方默认 retry_on 对 OSError 子类、HTTP 库非 5xx 错误不重试，而 LLM 网络/连接/超时
    异常多属此类，故这里显式匹配：节点超时、网络/连接、限流、5xx。确定性错误不重试。
    """
    if isinstance(exc, NodeTimeoutError):
        return True
    code = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if code and 500 <= int(code) < 600:
        return True
    name = type(exc).__name__
    if name in {"ValueError", "TypeError", "ArithmeticError", "KeyError", "StopIteration"}:
        return False
    s = f"{name} {exc}".lower()
    return any(
        k in s
        for k in (
            "timeout",
            "connection",
            "network",
            "unavailable",
            "temporary",
            "rate limit",
            "too many",
        )
    )


def _llm_retry() -> RetryPolicy:
    """瞬时错误重试（max_attempts=2）。"""
    return RetryPolicy(max_attempts=2, retry_on=_is_transient)


def _llm_timeout(config: TaskAgentConfig) -> float:
    """节点超时：留足客户端重试空间(llm_max_retries 次)后仍有限。"""
    return config.llm_timeout * (config.llm_max_retries + 1)


def _err_plan(state: dict, error: NodeError) -> Command:
    """plan LLM 失败 → 回退单一子任务(直接回答目标)，继续 execute。"""
    return Command(
        update={
            "plan": [
                {
                    "id": "1",
                    "desc": "请直接回答：" + state["goal"],
                    "status": "pending",
                    "result": "",
                }
            ],
            "current_idx": 0,
            "findings": [],
        },
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
    return Command(
        update={"should_retry": False, "retries": int(state.get("retries") or 0)},
        goto="check",
    )


# ---------------- 构建 ----------------


def build_agent(
    config: TaskAgentConfig,
    llm_factory: LLMFactory,
    checkpointer_provider: Callable[[], Any | None] = lambda: None,
    executor: Executor | None = None,
) -> Any:
    """构建编译后的 LangGraph。

    - config：模式/容错/步数等运行配置；
    - llm_factory：每次 LLM 调用时调用（返回带 async ainvoke 的对象）；
    - checkpointer_provider：返回 LangGraph checkpointer 或 None（无状态/HITL 降级）；
    - executor：每步执行器，缺省为纯 LLM 直答（DefaultExecutor）。
    """
    if executor is None:
        executor = DefaultExecutor(llm_factory)
    runtime = Runtime(
        config=config,
        llm_factory=llm_factory,
        executor=executor,
        checkpointer_provider=checkpointer_provider,
    )
    nodes = make_nodes(runtime)
    checkpointer = checkpointer_provider()
    retry = _llm_retry()
    timeout = _llm_timeout(config)

    if config.mode.lower() == "fixed":
        g = StateGraph(TaskState)
        g.add_node(
            "plan",
            nodes["plan_node"],
            retry_policy=retry,
            timeout=timeout,
            error_handler=_err_plan,
        )
        g.add_node("execute", nodes["execute_node"])
        g.add_node(
            "final",
            nodes["final_node"],
            retry_policy=retry,
            timeout=timeout,
            error_handler=_err_final,
        )
        g.add_edge(START, "plan")
        g.add_edge("plan", "execute")
        g.add_conditional_edges(
            "execute", _route_fixed, {"continue": "execute", "final": "final"}
        )
        g.add_edge("final", END)
        return g.compile(checkpointer=checkpointer)

    g = StateGraph(TaskState)
    g.add_node(
        "replan",
        nodes["replan_node"],
        retry_policy=retry,
        timeout=timeout,
        error_handler=_err_replan,
    )
    g.add_node("confirm", nodes["human_confirm_node"])  # 节点级 HITL(关闭时透传)
    g.add_node("execute", nodes["execute_action_node"])
    g.add_node(
        "verify",
        nodes["verify_node"],
        retry_policy=retry,
        timeout=timeout,
        error_handler=_err_verify,
    )
    g.add_node(
        "check",
        nodes["check_node"],
        retry_policy=retry,
        timeout=timeout,
        error_handler=_err_check,
    )
    g.add_node(
        "final",
        nodes["final_node"],
        retry_policy=retry,
        timeout=timeout,
        error_handler=_err_final,
    )
    g.add_edge(START, "replan")
    g.add_conditional_edges(
        "replan", _route_after_replan, {"final": "final", "confirm": "confirm"}
    )
    g.add_conditional_edges(
        "confirm", _route_after_confirm, {"execute": "execute", "check": "check"}
    )
    g.add_conditional_edges(
        "execute",
        _route_after_execute(config),
        {"verify": "verify", "check": "check"},
    )
    g.add_conditional_edges(
        "verify", _route_after_verify, {"execute": "execute", "check": "check"}
    )
    g.add_conditional_edges(
        "check", _route_replan, {"final": "final", "replan": "replan"}
    )
    g.add_edge("final", END)
    return g.compile(checkpointer=checkpointer)


async def list_task_history(graph: Any, session_id: str, limit: int = 30) -> list[dict]:
    """Time Travel：列出自主任务线程的 checkpoint 历史(新→旧)。

    需传入带 checkpointer 的编译图；graph 为 None 或无状态图时返回空。
    每条含 checkpoint_id(可 replay / fork)、parent_checkpoint_id、创建时间、
    next(待执行节点)、摘要(final_answer 或最后一条 finding)、是否处于中断等待确认。
    """
    if graph is None or not session_id:
        return []
    result: list[dict] = []
    try:
        async for snap in graph.aget_state_history(
            {"configurable": {"thread_id": session_id}}
        ):
            cfg = (snap.config or {}).get("configurable", {}) or {}
            parent_cfg = (snap.parent_config or {}).get("configurable", {}) or {}
            values = snap.values or {}
            summary = values.get("final_answer") or _last_finding(values.get("findings"))
            created = getattr(snap, "created_at", None)
            iso = (
                created.isoformat()
                if created is not None and hasattr(created, "isoformat")
                else None
            )
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
