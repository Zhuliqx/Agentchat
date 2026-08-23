"""自主任务 Agent 的图节点：plan / execute / final。

- plan_node   ：LLM 把目标拆成子任务列表；
- execute_node：对当前子任务调用一次 supervisor（复用现有子 Agent 能力）执行；
- final_node  ：整合所有子任务结果，输出最终交付。
一期为最小闭环，不含 verify / HITL（二期）。
"""
from __future__ import annotations

import json
import uuid

from app.agents.llm import get_llm
from app.task_agent.prompts import CHECK_PROMPT, FINAL_PROMPT, PLAN_PROMPT, REPLAN_PROMPT


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
    try:
        resp = await get_llm("light").ainvoke(PLAN_PROMPT.format(goal=state["goal"]))
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        plan = _parse_plan(text)
    except Exception:  # noqa: BLE001 - LLM 失败回退单任务,不中断
        plan = []
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
    return {"plan": plan, "findings": state["findings"] + [finding], "current_idx": idx + 1}


async def final_node(state: dict) -> dict:
    goal = state["goal"]
    findings = "\n\n".join(f"[{i + 1}] {f[:400]}" for i, f in enumerate(state.get("findings", [])))
    try:
        resp = await get_llm("light").ainvoke(FINAL_PROMPT.format(goal=goal, findings=findings or "（无）"))
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        final = (text or "").strip()
    except Exception:  # noqa: BLE001 - 整合失败给出兜底
        final = "任务已执行，但结果整合失败。"
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
    """基于 goal + findings 决定下一步动作(每步动态)。"""
    goal = state["goal"]
    findings = state.get("findings") or []
    try:
        resp = await get_llm("light").ainvoke(
            REPLAN_PROMPT.format(goal=goal, findings=_fmt_findings(findings) or "（尚无）")
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        action, source = _parse_next_action(text)
    except Exception:  # noqa: BLE001 - 规划失败 → 结束
        action, source = "", "default"
    if not action:
        # 无下一步 → 视为可完成
        return {"current_action": "", "done": True}
    return {"current_action": action, "expected_source": source}


async def execute_action_node(state: dict) -> dict:
    """执行当前 current_action，结果追加到 findings，步数 +1。"""
    action = state.get("current_action") or ""
    if not action:
        return {"step": int(state.get("step") or 0) + 1}  # 空动作：跳过执行只推进步数}
    findings = list(state.get("findings") or [])
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
    except Exception as exc:  # noqa: BLE001 - 单步失败不中断
        finding = f"子任务失败：{exc}"
    findings.append(finding)
    return {"findings": findings, "step": int(state.get("step") or 0) + 1}


async def check_node(state: dict) -> dict:
    """判断是否完成；LLM 判语义达成 + 规则兜底步数上限。"""
    step = int(state.get("step") or 0)
    if step >= MAX_STEPS:
        return {"done": True}
    goal = state["goal"]
    findings = state.get("findings") or []
    try:
        resp = await get_llm("light").ainvoke(
            CHECK_PROMPT.format(goal=goal, findings=_fmt_findings(findings) or "（尚无）")
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        done = _parse_done(text)
    except Exception:  # noqa: BLE001 - 检查失败安全收敛
        done = True
    return {"done": done}