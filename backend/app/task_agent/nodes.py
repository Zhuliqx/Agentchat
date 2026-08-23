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
from app.task_agent.prompts import FINAL_PROMPT, PLAN_PROMPT


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