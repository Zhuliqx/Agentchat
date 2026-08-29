"""工具调用执行器（ToolCallingExecutor）单元测试。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from task_agent.executor import ExecuteRequest
from task_agent.tools import Tool, ToolCallingExecutor, _safe_calc, builtin_tools


class _SeqLLM:
    """按顺序返回预设 JSON 的假 LLM（最后一次重复）。"""

    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)

    async def ainvoke(self, prompt: str) -> Any:
        if self._contents:
            return SimpleNamespace(content=self._contents.pop(0))
        return SimpleNamespace(content='{"answer": "完成"}')


def _run(executor, action="计算 1+2*3"):
    return asyncio.run(executor(ExecuteRequest(action=action)))


def test_executor_runs_tool_then_answers():
    llm = _SeqLLM(
        '{"tool": "calculator", "args": {"expression": "1 + 2 * 3"}}',
        '{"answer": "结果是 7"}',
    )
    ex = ToolCallingExecutor(lambda: llm, builtin_tools)
    r = _run(ex)
    assert "7" in r.answer
    assert "[已调用] calculator=7" in r.answer  # 工具轨迹可见


def test_executor_direct_answer():
    llm = _SeqLLM('{"answer": "直接回答"}')
    ex = ToolCallingExecutor(lambda: llm, builtin_tools)
    assert _run(ex).answer == "直接回答"


def test_executor_unknown_tool_friendly_error():
    llm = _SeqLLM('{"tool": "not_exist", "args": {}}')
    ex = ToolCallingExecutor(lambda: llm, builtin_tools)
    r = _run(ex)
    assert "not_exist" in r.answer and "工具不存在" in r.answer


def test_executor_tool_failure_reported():
    def _boom() -> str:
        raise ValueError("表达式非法")

    llm = _SeqLLM('{"tool": "bad", "args": {}}')
    ex = ToolCallingExecutor(
        lambda: llm,
        [Tool("bad", "必炸工具", func=_boom)],
    )
    r = _run(ex)
    assert "执行失败" in r.answer and "表达式非法" in r.answer


def test_executor_async_tool():
    async def _async_tool(x: str = "") -> str:
        return f"async-{x}"

    llm = _SeqLLM('{"tool": "a", "args": {"x": "ok"}}', '{"answer": "收尾"}')
    ex = ToolCallingExecutor(
        lambda: llm,
        [Tool("a", "异步工具", {"x": {"type": "string"}}, _async_tool)],
    )
    r = _run(ex)
    assert "async-ok" in r.answer


def test_safe_calc_rejects_io():
    assert _safe_calc("1 + 2") == "3"
    assert "计算失败" in _safe_calc("__import__('os')")
    assert "计算失败" in _safe_calc("open('/etc/passwd')")
