"""深度工程化测试：容错（混沌/错误注入）、findings 压缩、事件流。"""
from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import Any

import pytest

from task_agent.config import TaskAgentConfig
from task_agent.executor import ExecuteRequest, StepResult
from task_agent.graph import build_agent


class _ReplanLLM:
    """脚本化 replan：N 个动作后空动作结束；check 前 N 次 false 后 true。"""

    def __init__(self, actions: list[str] = ("动作一", "动作二"), check_turns: int = 2) -> None:
        self._actions = list(actions)
        self._replan_calls = 0
        self._check_calls = 0
        self._verify_calls = 0

    async def ainvoke(self, prompt: str) -> Any:
        if "可用信息来源" in prompt:  # REPLAN
            self._replan_calls += 1
            if self._replan_calls <= len(self._actions):
                return SimpleNamespace(
                    content=(
                        '{"next_action": "%s", "expected_source": "kb"}'
                        % self._actions[self._replan_calls - 1]
                    )
                )
            return SimpleNamespace(content='{"next_action": "", "expected_source": "default"}')
        if "完成度检查员" in prompt:  # CHECK
            self._check_calls += 1
            return SimpleNamespace(
                content='{"done": false}'
                if self._check_calls < len(self._actions)
                else '{"done": true}'
            )
        if "质检员" in prompt:  # VERIFY
            self._verify_calls += 1
            return SimpleNamespace(content='{"retry": true, "reason": "临时故障"}')
        if "结果整合器" in prompt:  # FINAL
            return SimpleNamespace(content="最终交付：目标已完成。")
        return SimpleNamespace(content="执行结果。")


class _FixedLLM:
    """脚本化 fixed：计划 2 个子任务，最终汇总。"""

    async def ainvoke(self, prompt: str) -> Any:
        if "任务规划器" in prompt:
            return SimpleNamespace(
                content='[{"id": "1", "desc": "子任务一"}, {"id": "2", "desc": "子任务二"}]'
            )
        if "结果整合器" in prompt:
            return SimpleNamespace(content="最终交付：目标已完成。")
        return SimpleNamespace(content="执行结果。")


class _FlakyExecutor:
    """按概率失败的执行器（seed 固定，可复现）。"""

    def __init__(self, fail_rate: float, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.fail_rate = fail_rate

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        if self._rng.random() < self.fail_rate:
            raise RuntimeError("模拟网络抖动")
        return StepResult(answer="执行成功。")


class _AlwaysFailExecutor:
    async def __call__(self, request: ExecuteRequest) -> StepResult:
        raise RuntimeError("执行器永久故障")


class _EmptyExecutor:
    """始终返回空答案（无输出）的执行器。"""

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        return StepResult(answer="")


class _BrokenLLM:
    async def ainvoke(self, prompt: str) -> Any:
        raise RuntimeError("LLM 永久故障")


def _run(agent, goal: str) -> dict:
    return asyncio.run(agent.ainvoke({"goal": goal}))


def _replan_agent(config: TaskAgentConfig | None = None, **kw):
    llm = _ReplanLLM()
    return build_agent(
        config=config or TaskAgentConfig(mode="replan", hitl=False),
        llm_factory=lambda: llm,
        checkpointer_provider=lambda: None,
        **kw,
    )


# ---------------- 容错（混沌注入） ----------------


def test_random_executor_failures_converge():
    """执行器 40% 概率失败：verify 重试 + check 兜底，最终总能交付且不超步数。"""
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False, max_retries=2, max_steps=8),
        llm_factory=lambda: _ReplanLLM(actions=("a", "b")),
        checkpointer_provider=lambda: None,
        executor=_FlakyExecutor(fail_rate=0.4, seed=7),
    )
    result = _run(agent, "目标")
    assert result.get("final_answer")
    assert int(result.get("step") or 0) <= 8
    assert result.get("findings")


def test_executor_always_fails_still_finishes():
    """执行器永久失败：verify 重试到上限后放弃，check 判完成，final 兜底交付。"""
    llm = _ReplanLLM()
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False, max_retries=2, max_steps=8),
        llm_factory=lambda: llm,
        checkpointer_provider=lambda: None,
        executor=_AlwaysFailExecutor(),
    )
    result = _run(agent, "目标")
    assert result.get("final_answer")
    assert any("子任务失败" in f for f in result.get("findings") or [])
    assert llm._verify_calls >= 2  # 重试机制被真正触发


def test_empty_answer_no_infinite_verify_loop():
    """空答案视为失败且 retries 不归零：verify 到上限后放弃，不会无限循环。"""
    llm = _ReplanLLM(actions=("a",))
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False, max_retries=2, max_steps=4),
        llm_factory=lambda: llm,
        checkpointer_provider=lambda: None,
        executor=_EmptyExecutor(),
    )
    result = _run(agent, "目标")
    assert result.get("final_answer")
    assert llm._verify_calls == 2  # 重试到上限即放弃，而非无限循环
    assert int(result.get("step") or 0) == 1  # 仅首次尝试计步，重试不计步


def test_llm_total_failure_degrades_gracefully():
    """LLM 永久失败：plan/replan/check/final 的 error_handler 依次降级，不抛异常。"""
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False, max_steps=4),
        llm_factory=lambda: _BrokenLLM(),
        checkpointer_provider=lambda: None,
    )
    result = _run(agent, "目标")
    assert result.get("final_answer")  # _err_final 兜底文本
    assert "失败" in result["final_answer"] or result["final_answer"].strip()


# ---------------- findings 压缩（长任务记忆治理） ----------------


def test_findings_budget_compresses_history():
    """findings_budget=3：执行 5 步后 findings 不超 3 条，历史被压进 findings_summary。"""
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=False, findings_budget=3, max_steps=8),
        llm_factory=lambda: _ReplanLLM(actions=("a", "b", "c", "d", "e"), check_turns=5),
        checkpointer_provider=lambda: None,
    )
    result = _run(agent, "目标")
    assert len(result.get("findings") or []) <= 3
    assert result.get("findings_summary")  # 历史摘要已生成


def test_findings_budget_none_keeps_all():
    agent = _replan_agent(TaskAgentConfig(mode="replan", hitl=False))
    result = _run(agent, "目标")
    assert result.get("findings_summary") in (None, "")


def test_findings_budget_validation():
    with pytest.raises(ValueError):
        TaskAgentConfig(mode="replan", findings_budget=0)


# ---------------- 事件流 ----------------


def test_on_event_lifecycle():
    """on_event 回调收到 plan 生命周期事件（replan/execute/check/final），无 HITL 事件。"""
    events: list[tuple[str, dict]] = []
    agent = _replan_agent(on_event=lambda kind, data: events.append((kind, data)))
    result = _run(agent, "目标")
    kinds = [k for k, _ in events]
    assert "replan" in kinds and "execute" in kinds and "check" in kinds and "final" in kinds
    assert "hitl" not in kinds  # hitl=False
    assert result.get("final_answer")


def test_on_event_hitl_when_enabled_with_checkpointer():
    """开启 HITL 且注入 checkpointer 时，human_confirm_node 发 hitl 事件。"""
    import langgraph.checkpoint.memory as mem

    events: list[tuple[str, dict]] = []
    agent = build_agent(
        config=TaskAgentConfig(mode="replan", hitl=True),
        llm_factory=lambda: _ReplanLLM(actions=("a",)),
        checkpointer_provider=lambda: mem.InMemorySaver(),
        on_event=lambda kind, data: events.append((kind, data)),
    )
    result = asyncio.run(
        agent.ainvoke(
            {"goal": "目标"},
            config={"configurable": {"thread_id": "hitl-test"}},
        )
    )
    # 图停在 interrupt 等待确认：事件应包含 hitl，且返回 pending
    assert any(k == "hitl" for k, _ in events)
    assert result.get("__interrupt__")
