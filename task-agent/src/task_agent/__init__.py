"""自主任务 Agent（独立包，零业务依赖）。

面向模糊长目标的多步自主执行引擎：接收目标 → 每步重规划（或一次计划）→
循环执行（注入 Executor）→ 完成度检查 → 结构化交付。

使用：
    from task_agent import TaskAgentConfig, build_agent

    agent = build_agent(
        config=TaskAgentConfig(mode="replan"),
        llm_factory=llm_factory,                      # Callable[[], LLM]
        checkpointer_provider=checkpointer_provider,  # Callable[[], Any | None]
        executor=executor,                            # Callable[[ExecuteRequest], Awaitable[StepResult]]
    )
    result = await agent.ainvoke({"goal": "..."})
"""
from task_agent.config import TaskAgentConfig
from task_agent.executor import (
    DefaultExecutor,
    ExecuteRequest,
    Executor,
    SOURCE_KEYS,
    StepResult,
)
from task_agent.graph import build_agent, list_task_history

__all__ = [
    "TaskAgentConfig",
    "build_agent",
    "list_task_history",
    "ExecuteRequest",
    "StepResult",
    "Executor",
    "DefaultExecutor",
    "SOURCE_KEYS",
]
