"""任务级质量评估（LLM-judge，自包含）。

对一次任务执行的目标达成度 / 信息完整性 / 幻觉打分（0-1），供基准与宿主评估复用。
解析失败或 LLM 异常时安全返回 0（不中断调用方）。
"""
from __future__ import annotations

from task_agent.llm import LLM, llm_text
from task_agent.nodes import _fmt_findings, _jump_json
from task_agent.prompts import EVAL_PROMPT


def _norm(score: object) -> float:
    """0-5 分归一化到 0-1（非法/缺失 → 0.0）。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, s / 5.0))


async def judge_task(
    llm: LLM,
    goal: str,
    findings: list[str],
    final_answer: str,
) -> dict:
    """评估一次任务执行；返回 {goal_attainment, info_completeness, hallucination, comment}。"""
    try:
        text = (
            await llm_text(
                llm,
                EVAL_PROMPT.format(
                    goal=goal,
                    findings=_fmt_findings(findings) or "（无）",
                    final_answer=final_answer or "（无）",
                ),
            )
        ).strip()
        data = _jump_json(text)
    except Exception:  # noqa: BLE001 - 评估失败不中断基准
        data = {}
    return {
        "goal_attainment": _norm(data.get("goal_attainment")),
        "info_completeness": _norm(data.get("info_completeness")),
        "hallucination": _norm(data.get("hallucination")),
        "comment": str(data.get("comment") or ""),
    }
