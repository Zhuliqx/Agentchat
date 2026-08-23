"""自主任务 Agent 的图节点：plan / execute / final / replan / check / verify / human_confirm。

- plan_node   ：LLM 把目标拆成子任务列表（fixed 一期）；
- execute_node：对当前子任务调用一次 supervisor 执行（fixed 一期；顺序、含失败标记）；
- final_node  ：整合所有子任务结果，输出最终交付；
- replan_node ：每步动态决定下一步动作 + 标注信息来源（replan 二期）；
- execute_action_node：按信息来源路由执行当前动作；
- check_node  ：判断是否充分达成目标；
- verify_node ：子任务失败后的自检（是否值得重试）——节点容错；
- human_confirm_node：节点级 HITL——让用户确认/编辑/跳过下一步动作（可关）。
"""
from __future__ import annotations

import json
import uuid

from langgraph.types import interrupt

from app.agents.llm import get_llm
from app.config import settings
from app.task_agent.prompts import (
    CHECK_PROMPT,
    FINAL_PROMPT,
    PLAN_PROMPT,
    REPLAN_PROMPT,
    VERIFY_PROMPT,
)


async def _llm_text(prompt: str, model: str = "light") -> str:
    """单次 LLM 调用，失败直接抛异常——重试与降级交由节点级 retry_policy / error_handler 处理。"""
    resp = await get_llm(model).ainvoke(prompt)
    return resp.content if isinstance(resp.content, str) else str(resp.content)


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
            out.append({"id": str(item.get("id") or i + 1), "desc": item["desc"],
                        "status": "pending", "result": ""})
    return out


async def plan_node(state: dict) -> dict:
    plan = _parse_plan(await _llm_text(PLAN_PROMPT.format(goal=state["goal"])))
    if not plan:
        plan = [{"id": "1", "desc": "请直接回答：" + state["goal"], "status": "pending", "result": ""}]
    return {"plan": plan, "current_idx": 0, "findings": []}


async def execute_node(state: dict) -> dict:
    idx = state["current_idx"]
    plan = [dict(p) for p in state["plan"]]
    if idx >= len(plan):
        return {"current_idx": idx}
    task = plan[idx]
    try:
        from app.agents.graph import run_agent

        result = await run_agent(
            question=task["desc"],
            use_rag=True,
            use_search=True,
            use_memory=False,
            session_id=f"sub-{uuid.uuid4().hex[:8]}",
            user_id="default",
            resume=None,
            checkpoint_id=None,
            on_event=None,
        )
        finding = (result.get("answer", "") or "（子任务无输出）")[:800]
        plan[idx]["status"] = "done"
    except Exception as exc:  # noqa: BLE001 - 单子任务失败不中断整个任务
        finding = f"子任务失败：{exc}"
        plan[idx]["status"] = "failed"
    plan[idx]["result"] = finding
    return {"plan": plan, "findings": [finding], "current_idx": idx + 1}


async def final_node(state: dict) -> dict:
    goal = state["goal"]
    findings = "\n\n".join(f"[{i + 1}] {f[:400]}" for i, f in enumerate(state.get("findings", [])))
    final = (await _llm_text(FINAL_PROMPT.format(goal=goal, findings=findings or "（无）"))).strip()
    return {"final_answer": final}
# ---------------- 二期：每步 re-plan + 独立 check ----------------

MAX_STEPS = 8

# replan 标注的来源 → 执行层收紧开关 + 前缀引导(设计 A/务实版)
_SOURCE_ROUTE = {
    "kb":      {"use_rag": True,  "use_search": False, "prefix": "请用知识库查询："},
    "web":     {"use_rag": False, "use_search": True,  "prefix": "请联网搜索："},
    "db":      {"use_rag": False, "use_search": False, "prefix": "请查数据库："},
    "code":    {"use_rag": False, "use_search": False, "prefix": "请用代码计算："},
    "default": {"use_rag": True,  "use_search": True,  "prefix": ""},
}


def _fmt_findings(findings: list[str]) -> str:
    """把结果列表格式化为 replan/check 的上下文文本(截断)。"""
    return "\n\n".join(f"[{i + 1}] {f[:400]}" for i, f in enumerate(findings or []))


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
    return action, source if source in _SOURCE_ROUTE else "default"


def _parse_done(text: str) -> bool:
    return bool(_jump_json(text).get("done", False))


async def replan_node(state: dict) -> dict:
    """基于 goal + findings 决定下一步动作(每步动态)。新动作 → 重试计数归零。"""
    goal = state["goal"]
    findings = state.get("findings") or []
    action, source = _parse_next_action(
        await _llm_text(REPLAN_PROMPT.format(goal=goal, findings=_fmt_findings(findings) or "（尚无）"))
    )
    if not action:
        # 无下一步 → 视为可完成
        return {"current_action": "", "done": True, "retries": 0}
    return {"current_action": action, "expected_source": source, "retries": 0}


async def execute_action_node(state: dict) -> dict:
    """执行当前 current_action，结果追加到 findings；支持重试(retries>0 时步数不增)。"""
    action = state.get("current_action") or ""
    retries = int(state.get("retries") or 0)
    is_retry = retries > 0
    if not action:
        return {"step": int(state.get("step") or 0) + 1}  # 空动作：跳过执行只推进步数
    try:
        from app.agents.graph import run_agent

        route = _SOURCE_ROUTE.get(state.get("expected_source") or "default") or _SOURCE_ROUTE["default"]
        q = (route["prefix"] + action) if route["prefix"] else action
        result = await run_agent(
            question=q,
            use_rag=route["use_rag"],
            use_search=route["use_search"],
            use_memory=False,
            session_id=f"sub-{uuid.uuid4().hex[:8]}",
            user_id="default",
            resume=None,
            checkpoint_id=None,
            on_event=None,
        )
        finding = (result.get("answer", "") or "（子任务无输出）")[:800]
        new_retries = 0  # 成功 → 重试计数归零
    except Exception as exc:  # noqa: BLE001 - 单步失败不中断
        finding = f"子任务失败：{exc}"
        new_retries = retries  # 失败 → 保留计数(由 verify 决定是否 +1 / 放弃)
    step = int(state.get("step") or 0) + (0 if is_retry else 1)
    return {"findings": [finding], "step": step, "retries": new_retries}


async def check_node(state: dict) -> dict:
    """判断是否完成；LLM 判语义达成 + 规则兜底步数上限。"""
    step = int(state.get("step") or 0)
    if step >= MAX_STEPS:
        return {"done": True}
    goal = state["goal"]
    findings = state.get("findings") or []
    done = _parse_done(
        await _llm_text(CHECK_PROMPT.format(goal=goal, findings=_fmt_findings(findings) or "（尚无）"))
    )
    return {"done": done}


def _is_failed_finding(finding: str) -> bool:
    """规则预筛：判断一条子任务结果是否属失败/无输出（触发 verify 自检）。"""
    f = (finding or "").strip()
    return not f or f.startswith("子任务失败") or f == "（子任务无输出）"


async def verify_node(state: dict) -> dict:
    """自检：子任务失败后判断是否值得重试(节点容错)。达到重试上限则放弃。"""
    goal = state["goal"]
    action = state.get("current_action") or ""
    findings = state.get("findings") or []
    finding = findings[-1] if findings else ""
    retries = int(state.get("retries") or 0)
    if retries >= settings.task_agent_max_retries:
        return {"should_retry": False, "retries": retries}
    data = _jump_json(
        await _llm_text(
            VERIFY_PROMPT.format(
                goal=goal, action=action, finding=finding,
                findings=_fmt_findings(findings) or "（无）",
            )
        )
    )
    retry = bool(data.get("retry", False))
    if retry:
        return {"should_retry": True, "retries": retries + 1}
    return {"should_retry": False, "retries": 0}


async def human_confirm_node(state: dict) -> dict:
    """节点级 HITL：replan 产出下一步后让用户确认/编辑/跳过。

    - 关闭(task_agent_hitl=False) → 透传 proceed,不 interrupt;
    - 开启 → interrupt 暂停,用户经 agent-tasks/confirm 用 Command(resume=decision) 恢复。
    决策 verb: proceed(执行) / edit(改用用户给的动作/来源) / skip(跳过此步,进 check)。
    """
    if not settings.task_agent_hitl:
        return {"_confirm_verb": "proceed"}
    # HITL 依赖 checkpointer 持久化(interrupt/resume 需要从 checkpoint 恢复)；未连
    # Postgres 时禁用，避免中断后无法恢复 → 降级为全自主(与关闭 HITL 一致)。
    from app.db.memory_store import get_checkpointer

    if get_checkpointer() is None:
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
    return _apply_confirm(decision, state.get("current_action"), state.get("expected_source"))


def _apply_confirm(decision: dict, current_action: str, expected_source: str) -> dict:
    """把 HITL 决策落到 state 增量(纯函数,便于单测)。"""
    verb = str((decision or {}).get("verb") or "proceed").lower()
    if verb == "edit":
        return {
            "_confirm_verb": "edit",
            "current_action": str((decision or {}).get("action") or current_action or ""),
            "expected_source": str((decision or {}).get("source") or expected_source or "default"),
        }
    if verb == "skip":
        return {"_confirm_verb": "skip"}
    return {"_confirm_verb": "proceed"}