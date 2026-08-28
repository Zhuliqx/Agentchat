"""每步执行接口与默认实现（纯 LLM 直答）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from task_agent.llm import LLMFactory, llm_text

Source = Literal["kb", "db", "web", "code", "default"]
SOURCE_KEYS: tuple[Source, ...] = ("kb", "db", "web", "code", "default")


@dataclass(frozen=True)
class ExecuteRequest:
    """一步动作及其信息源提示（供宿主决定启用哪些工具/开关）。"""

    action: str
    source: Source = "default"


@dataclass(frozen=True)
class StepResult:
    """一步执行的结构化结果（answer 为供整合的文本）。"""

    answer: str


class Executor(Protocol):
    """执行一步动作。失败应抛异常（由节点捕获并标记为该步失败）。"""

    async def __call__(self, request: ExecuteRequest) -> StepResult: ...


class DefaultExecutor:
    """默认执行器：不调用任何工具，直接由 LLM 回答当前动作（纯 LLM 直答）。"""

    def __init__(self, llm_factory: LLMFactory) -> None:
        self._llm_factory = llm_factory

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        answer = (await llm_text(self._llm_factory(), request.action)).strip()
        return StepResult(answer=answer)
