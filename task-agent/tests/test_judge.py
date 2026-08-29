"""任务级质量评估（LLM-judge）单元测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from task_agent.judge import judge_task


class _JudgeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def ainvoke(self, prompt: str) -> Any:
        return SimpleNamespace(content=self._content)


class _BrokenJudgeLLM:
    async def ainvoke(self, prompt: str) -> Any:
        raise RuntimeError("judge 挂了")


def _judge(llm, goal="目标", findings=None, answer="答案"):
    return asyncio.run(
        judge_task(llm, goal, findings or ["记录一"], answer)
    )


def test_judge_parses_and_normalizes_scores():
    llm = _JudgeLLM(
        '{"goal_attainment": 5, "info_completeness": 3, "hallucination": 1, "comment": "达成良好"}'
    )
    r = _judge(llm)
    assert r["goal_attainment"] == 1.0
    assert round(r["info_completeness"], 2) == 0.6
    assert round(r["hallucination"], 2) == 0.2
    assert r["comment"] == "达成良好"


def test_judge_malformed_json_safe_zero():
    llm = _JudgeLLM("抱歉，我无法评估。")
    r = _judge(llm)
    assert r["goal_attainment"] == 0.0
    assert r["info_completeness"] == 0.0
    assert r["hallucination"] == 0.0


def test_judge_llm_failure_safe_zero():
    r = _judge(_BrokenJudgeLLM())
    assert r == {
        "goal_attainment": 0.0,
        "info_completeness": 0.0,
        "hallucination": 0.0,
        "comment": "",
    }


def test_judge_clamps_out_of_range():
    llm = _JudgeLLM(
        '{"goal_attainment": 9, "info_completeness": -2, "hallucination": 5, "comment": ""}'
    )
    r = _judge(llm)
    assert r["goal_attainment"] == 1.0
    assert r["info_completeness"] == 0.0
    assert r["hallucination"] == 1.0
