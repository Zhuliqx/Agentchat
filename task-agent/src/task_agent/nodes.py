"""自主任务 Agent 的图节点（通过 Runtime 闭包注入配置/LLM/执行器）。

- plan_node           ：LLM 把目标拆成子任务列表（fixed 一期）；
- execute_node        ：对当前子任务调用一次 executor（fixed 一期；顺序、含失败标记）；
- final_node          ：整合所有子任务结果，输出最终交付；
- replan_node         ：每步动态决定下一步动作 + 标注信息来源（replan 二期）；
- execute_action_node ：按来源路由执行当前动作（调用注入的 Executor）；
- check_node          ：判断是否充分达成目标；
- verify_node         ：子任务失败后的自检（是否值得重试）——节点容错；
- human_confirm_node  ：节点级 HITL——让用户确认/编辑/跳过下一步动作（可关）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langgraph.types import interrupt

from task_agent.config import TaskAgentConfig
from task_agent.executor import SOURCE_KEYS, ExecuteRequest, Executor
from task_agent.llm import LLMFactory, llm_text
from task_agent.prompts import (
    CHECK_PROMPT,
    FINAL_PROMPT,
    PLAN_PROMPT,
    REPLAN_PROMPT,
    VERIFY_PROMPT,
)


@dataclass(frozen=True)
class Runtime:
    """构建图时注入的运行上下文（配置 + LLM 工厂 + 执行器 + checkpointer 提供者）。"""

    config: TaskAgentConfig
    llm_factory: LLMFactory
    executor: Executor
    checkpointer_provider: Callable[[], Any | None] = lambda: None


Node = Callable[[dict], Awaitable[dict]]


def make_nodes(runtime: Runtime) -> dict[str, Node]:
    """按 Runtime 生成全部节点（闭包注入，避免全局可变状态）。"""
    config = runtime.config

    async def plan_node(state: dict) -> dict:
        plan = _parse_plan(
            await llm_text(runtime.llm_factory(), PLAN_PROMPT.format(goal=state["goal"]))
        )
        if not plan:
            plan = [
                {
                    "id": "1",
                    "desc": "请直接回答：" + state["goal"],
                    "status": "pending",
                    "result": "",
                }
            ]
        return {"plan": plan, "current_idx": 0, "findings": []}

    async def execute_node(state: dict) -> dict:
        idx = state["current_idx"]
        plan = [dict(p) for p in state["plan"]]
        if idx >= len(plan):
            return {"current_idx": idx}
        task = plan[idx]
        try:
            result = await runtime.executor(
                ExecuteRequest(action=task["desc"], source="default")
            )
            finding = (result.answer or "（子任务无输出）")[:800]
            plan[idx]["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - 单子任务失败不中断整个任务
            finding = f"子任务失败：{exc}"
            plan[idx]["status"] = "failed"
        plan[idx]["result"] = finding
        return {"plan": plan, "findings": [finding], "current_idx": idx + 1}

    async def final_node(state: dict) -> dict:
        goal = state["goal"]
        findings = "\n\n".join(
            f"[{i + 1}] {f[:400]}"
            for i, f in enumerate(state.get("findings", []))
        )
        final = (
            await llm_text(
                runtime.llm_factory(),
                FINAL_PROMPT.format(goal=goal, findings=findings or "（无）"),
            )
        ).strip()
        return {"final_answer": final}

    async def replan_node(state: dict) -> dict:
        goal = state["goal"]
        findings = state.get("findings") or []
        action, source = _parse_next_action(
            await llm_text(
                runtime.llm_factory(),
                REPLAN_PROMPT.format(
                    goal=goal, findings=_fmt_findings(findings) or "（尚无）"
                ),
            )
        )
        if not action:
            # 无下一步 → 视为可完成
            return {"current_action": "", "done": True, "retries": 0}
        return {"current_action": action, "expected_source": source, "retries": 0}

    async def execute_action_node(state: dict) -> dict:
        action = state.get("current_action") or ""
        retries = int(state.get("retries") or 0)
        is_retry = retries > 0
        if not action:
            # 空动作：跳过执行只推进步数
            return {"step": int(state.get("step") or 0) + 1}
        try:
            result = await runtime.executor(
                ExecuteRequest(
                    action=action,
                    source=state.get("expected_source") or "default",
                )
            )
            finding = (result.answer or "（子任务无输出）")[:800]
            new_retries = 0  # 成功 → 重试计数归零
        except Exception as exc:  # noqa: BLE001 - 单步失败不中断
            finding = f"子任务失败：{exc}"
            new_retries = retries  # 失败 → 保留计数(由 verify 决定是否 +1 / 放弃)
        step = int(state.get("step") or 0) + (0 if is_retry else 1)
        return {"findings": [finding], "step": step, "retries": new_retries}

    async def check_node(state: dict) -> dict:
        step = int(state.get("step") or 0)
        if step >= config.max_steps:
            return {"done": True}
        goal = state["goal"]
        findings = state.get("findings") or []
        done = _parse_done(
            await llm_text(
                runtime.llm_factory(),
                CHECK_PROMPT.format(
                    goal=goal, findings=_fmt_findings(findings) or "（尚无）"
                ),
            )
        )
        return {"done": done}

    async def verify_node(state: dict) -> dict:
        goal = state["goal"]
        action = state.get("current_action") or ""
        findings = state.get("findings") or []
        finding = findings[-1] if findings else ""
        retries = int(state.get("retries") or 0)
        if retries >= config.max_retries:
            return {"should_retry": False, "retries": retries}
        data = _jump_json(
            await llm_text(
                runtime.llm_factory(),
                VERIFY_PROMPT.format(
                    goal=goal,
                    action=action,
                    finding=finding,
                    findings=_fmt_findings(findings) or "（无）",
                ),
            )
        )
        retry = bool(data.get("retry", False))
        if retry:
            return {"should_retry": True, "retries": retries + 1}
        return {"should_retry": False, "retries": 0}

    async def human_confirm_node(state: dict) -> dict:
        """节点级 HITL：replan 产出下一步后让用户确认/编辑/跳过。

        - 关闭(config.hitl=False) → 透传 proceed,不 interrupt；
        - 开启 → interrupt 暂停,宿主经 resume(decision) 恢复；
        - 依赖 checkpointer 持久化；无 checkpointer 时禁用，避免中断后无法恢复
          → 降级为全自主(与关闭 HITL 一致)。
        """
        if not config.hitl:
            return {"_confirm_verb": "proceed"}
        if runtime.checkpointer_provider() is None:
            return {"_confirm_verb": "proceed"}
        decision = interrupt(
            {
                "type": "task_confirm",
                "goal": state.get("goal", ""),
                "next_action": state.get("current_action") or "",
                "expected_source": state.get("expected_source") or "default",
                "step": state.get("step") or 0,
                "findings": state.get("findings") or [],
            }
        )
        return _apply_confirm(
            decision, state.get("current_action"), state.get("expected_source")
        )

    return {
        "plan_node": plan_node,
        "execute_node": execute_node,
        "final_node": final_node,
        "replan_node": replan_node,
        "execute_action_node": execute_action_node,
        "check_node": check_node,
        "verify_node": verify_node,
        "human_confirm_node": human_confirm_node,
    }


# ---------------- 纯函数辅助（便于单测） ----------------


def _parse_plan(text: str) -> list[dict]:
    """从 LLM 输出解析子任务数组；失败返回空（由下游标记为空计划）。"""
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("```"))
    try:
        begin, end = s.index("["), s.rindex("]")
        data = json.loads(s[begin : end + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    out = []
    for i, item in enumerate(data or []):
        if isinstance(item, dict) and item.get("desc"):
            out.append(
                {
                    "id": str(item.get("id") or i + 1),
                    "desc": item["desc"],
                    "status": "pending",
                    "result": "",
                }
            )
    return out


def _fmt_findings(findings: list[str]) -> str:
    """把结果列表格式化为 replan/check 的上下文文本(截断)。"""
    return "\n\n".join(
        f"[{i + 1}] {f[:400]}" for i, f in enumerate(findings or [])
    )


def _jump_json(text: str) -> dict:
    """剥离代码围栏后提取第一个 JSON 对象。"""
    s = (text or "").strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("```"))
    try:
        begin, end = s.index("{"), s.rindex("}")
        data = json.loads(s[begin : end + 1])
        return data if isinstance(data, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def _parse_next_action(text: str) -> tuple[str, str]:
    data = _jump_json(text)
    action = str(data.get("next_action", "") or "").strip()
    source = str(data.get("expected_source", "") or "").strip().lower()
    return action, source if source in SOURCE_KEYS else "default"


def _parse_done(text: str) -> bool:
    return bool(_jump_json(text).get("done", False))


def _is_failed_finding(finding: str) -> bool:
    """规则预筛：判断一条子任务结果是否属失败/无输出（触发 verify 自检）。"""
    f = (finding or "").strip()
    return not f or f.startswith("子任务失败") or f == "（子任务无输出）"


def _apply_confirm(decision: dict, current_action: str, expected_source: str) -> dict:
    """把 HITL 决策落到 state 增量(纯函数,便于单测)。"""
    verb = str((decision or {}).get("verb") or "proceed").lower()
    if verb == "edit":
        return {
            "_confirm_verb": "edit",
            "current_action": str(
                (decision or {}).get("action") or current_action or ""
            ),
            "expected_source": str(
                (decision or {}).get("source") or expected_source or "default"
            ),
        }
    if verb == "skip":
        return {"_confirm_verb": "skip"}
    return {"_confirm_verb": "proceed"}
