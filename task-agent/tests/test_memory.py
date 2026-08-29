"""跨任务记忆单元测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from task_agent.config import TaskAgentConfig
from task_agent.graph import build_agent
from task_agent.memory import InMemoryMemory, _tokens


class _RecordingLLM:
    """记录所有 prompt，并按阶段返回脚本化响应。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> Any:
        self.prompts.append(prompt)
        if "可用信息来源" in prompt:
            return SimpleNamespace(content='{"next_action": "查一下", "expected_source": "kb"}')
        if "完成度检查员" in prompt:
            return SimpleNamespace(content='{"done": true}')
        if "结果整合器" in prompt:
            return SimpleNamespace(content="结论：公司成立于2020年。")
        return SimpleNamespace(content="执行结果。")


def _run(agent, goal: str) -> dict:
    return asyncio.run(agent.ainvoke({"goal": goal}))


def test_in_memory_recall_overlap():
    mem = InMemoryMemory()
    asyncio.run(mem.remember("公司成立于哪一年", "成立于2020"))
    assert asyncio.run(mem.recall("公司的成立年份")) == ["成立于2020"]
    assert asyncio.run(mem.recall("质数和是多少")) == []


def test_in_memory_dedup_by_goal():
    mem = InMemoryMemory()
    asyncio.run(mem.remember("目标A", "结论1"))
    asyncio.run(mem.remember("目标A", "结论2"))
    assert len(mem.items()) == 1
    assert mem.items()[0][1] == "结论2"


def test_tokens_splits_chinese_and_english():
    t = _tokens("公司成立于2020年 RAG")
    assert "公司" in t and "成立" in t and "2020" in t and "rag" in t


def test_second_task_recalls_first_task_conclusion():
    """任务 2 的 replan 提示词应包含任务 1 的 final_answer（跨任务记忆）。"""
    mem = InMemoryMemory()
    llm1 = _RecordingLLM()
    agent1 = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False),
        llm_factory=lambda: llm1,
        checkpointer_provider=lambda: None,
        memory=mem,
    )
    _run(agent1, "公司成立于哪一年")

    llm2 = _RecordingLLM()
    agent2 = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False),
        llm_factory=lambda: llm2,
        checkpointer_provider=lambda: None,
        memory=mem,
    )
    _run(agent2, "公司的成立年份是多少")

    replan_prompts = [p for p in llm2.prompts if "可用信息来源" in p]
    assert replan_prompts
    assert "成立于2020" in replan_prompts[0]  # 历史任务知识被召回
    assert len(mem.items()) == 2  # 两次任务结论均已沉淀
