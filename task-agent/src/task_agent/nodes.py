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
from task_agent.memory import TaskMemory
from task_agent.prompts import (
    CHECK_PROMPT,
    COMPRESS_PROMPT,
    FINAL_PROMPT,
    PLAN_PROMPT,
    REPLAN_PROMPT,
    VERIFY_PROMPT,
)


@dataclass(frozen=True)
class Runtime:
    """构建图时注入的运行上下文（配置 + LLM 工厂 + 执行器 + checkpointer 提供者 + 事件回调）。"""

    config: TaskAgentConfig
    llm_factory: LLMFactory
    executor: Executor
    checkpointer_provider: Callable[[], Any | None] = lambda: None
    on_event: Callable[[str, dict], None] | None = None
    memory: TaskMemory | None = None

    def emit(self, kind: str, data: dict | None = None) -> None:
        """发事件（如 plan/replan/execute/check/verify/final/hitl），供宿主/日志观测。"""
        if self.on_event is not None:
            self.on_event(kind, data or {})


Node = Callable[[dict], Awaitable[dict]]


def make_nodes(runtime: Runtime) -> dict[str, Node]:
    """按 Runtime 生成全部节点（闭包注入，避免全局可变状态）。"""
    config = runtime.config

    async def plan_node(state: dict) -> dict:
        mem = await _memory_ctx(runtime, state["goal"])
        plan = _parse_plan(
            await llm_text(
                runtime.llm_factory(),
                PLAN_PROMPT.format(goal=state["goal"], memory=mem),
            )
        )
        fallback = False
        if not plan:
            fallback = True
            plan = [
                {
                    "id": "1",
                    "desc": "请直接回答：" + state["goal"],
                    "status": "pending",
                    "result": "",
                }
            ]
        runtime.emit("plan", {"subtasks": len(plan), "fallback": fallback})
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
            ok = bool(finding and finding.strip() and finding != "（子任务无输出）")
            plan[idx]["status"] = "done" if ok else "failed"
        except Exception as exc:  # noqa: BLE001 - 单子任务失败不中断整个任务
            finding = f"子任务失败：{exc}"
            plan[idx]["status"] = "failed"
            ok = False
        plan[idx]["result"] = finding
        runtime.emit("execute", {"subtask": task["desc"], "ok": ok})
        out: dict = {"plan": plan, "current_idx": idx + 1}
        out.update(await _append_finding(runtime, state, finding))
        return out

    async def final_node(state: dict) -> dict:
        goal = state["goal"]
        findings = "\n\n".join(
            f"[{i + 1}] {f[:400]}"
            for i, f in enumerate(state.get("findings", []))
        )
        summary = state.get("findings_summary") or ""
        if summary:
            findings = f"历史摘要：{summary}\n\n{findings}"
        final = (
            await llm_text(
                runtime.llm_factory(),
                FINAL_PROMPT.format(goal=goal, findings=findings or "（无）"),
            )
        ).strip()
        if runtime.memory is not None:
            try:
                await runtime.memory.remember(goal, final)
            except Exception:  # noqa: BLE001 - 记忆失败不影响交付
                pass
        runtime.emit("final", {"answer": final})
        return {"final_answer": final}

    async def replan_node(state: dict) -> dict:
        goal = state["goal"]
        findings = state.get("findings") or []
        mem = await _memory_ctx(runtime, goal)
        action, source = _parse_next_action(
            await llm_text(
                runtime.llm_factory(),
                REPLAN_PROMPT.format(
                    goal=goal,
                    findings=_fmt_findings(findings) or "（尚无）",
                    memory=mem,
                ),
            )
        )
        if not action:
            # 无下一步 → 视为可完成
            runtime.emit("replan", {"action": "", "source": source})
            return {"current_action": "", "done": True, "retries": 0}
        runtime.emit("replan", {"action": action, "source": source})
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
            # 空答案视为失败：否则 verify 判重试后 retries 又被归零，形成无限循环
            ok = bool(finding and finding.strip() and finding != "（子任务无输出）")
            new_retries = 0 if ok else retries
        except Exception as exc:  # noqa: BLE001 - 单步失败不中断
            finding = f"子任务失败：{exc}"
            new_retries = retries  # 失败 → 保留计数(由 verify 决定是否 +1 / 放弃)
            ok = False
        step = int(state.get("step") or 0) + (0 if is_retry else 1)
        runtime.emit("execute", {"action": action, "source": state.get("expected_source") or "default", "ok": ok})
        out: dict = {"step": step, "retries": new_retries}
        out.update(await _append_finding(runtime, state, finding))
        return out

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
        runtime.emit("check", {"done": done, "step": step})
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
        runtime.emit("verify", {"retry": retry, "retries": retries + 1 if retry else retries})
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
        runtime.emit(
            "hitl",
            {
                "next_action": state.get("current_action") or "",
                "expected_source": state.get("expected_source") or "default",
                "step": state.get("step") or 0,
            },
        )
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
            decision,
            state.get("current_action") or "",
            state.get("expected_source") or "default",
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


async def _compress_findings(
    runtime: Runtime, findings: list[str], summary: str
) -> tuple[list[str], str]:
    """超预算压缩：保留最新一条，历史交给 LLM 压缩进 findings_summary。

    LLM 失败时退化为截断拼接（不中断执行）。返回 (保留的 findings, 新摘要)。
    """
    keep = findings[-1:]
    older = findings[:-1]
    try:
        text = (
            await llm_text(
                runtime.llm_factory(),
                COMPRESS_PROMPT.format(
                    summary=summary or "（无）",
                    findings=_fmt_findings(older),
                ),
            )
        ).strip()
    except Exception:  # noqa: BLE001 - 压缩失败降级为截断
        text = ""
    if not text:
        text = "；".join(older)[:500]
    new_summary = f"{summary}；{text}" if summary else text
    return keep, new_summary[:1500]


async def _append_finding(runtime: Runtime, state: dict, finding: str) -> dict:
    """构造 findings 增量：未超预算 → 普通追加；超预算 → 整体替换 + 压缩摘要。"""
    budget = runtime.config.findings_budget
    existing = list(state.get("findings") or [])
    if budget is None or len(existing) + 1 <= budget:
        return {"findings": [finding]}
    keep, summary = await _compress_findings(
        runtime, existing + [finding], str(state.get("findings_summary") or "")
    )
    return {"findings": {"_replace": keep}, "findings_summary": summary}


async def _memory_ctx(runtime: Runtime, goal: str) -> str:
    """把跨任务记忆召回结果格式化为提示词上下文（无记忆/失败 → （无））。"""
    if runtime.memory is None:
        return "（无）"
    try:
        items = await runtime.memory.recall(goal)
    except Exception:  # noqa: BLE001 - 记忆不可用不影响执行
        return "（无）"
    return "\n".join(f"- {s}" for s in items) or "（无）"


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
