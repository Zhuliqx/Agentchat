"""宿主适配器：把主应用能力注入独立 task_agent 包。

- LLM        ：get_llm("light")（每次调用实时解析，模型切换后自动生效）；
- Checkpointer：get_checkpointer()（None 时包内自动降级无状态）；
- Executor    ：包装 run_agent，按 source 收紧 use_rag/use_search（沿用原 _SOURCE_ROUTE），
  每个执行步使用独立 thread（sub-<uuid>），与旧行为一致。
"""
from __future__ import annotations

import uuid
from typing import Any

from task_agent.config import TaskAgentConfig
from task_agent.executor import ExecuteRequest, StepResult
from task_agent.graph import build_agent, list_task_history  # noqa: F401 - 供路由统一导入

from app.agents.graph import run_agent
from app.agents.llm import get_llm
from app.config import settings
from app.db.memory_store import get_checkpointer

# replan 标注的信息来源 → 执行层收紧开关 + 前缀引导（原 task_agent.nodes._SOURCE_ROUTE）
_SOURCE_ROUTE: dict[str, dict] = {
    "kb": {"use_rag": True, "use_search": False, "prefix": "请用知识库查询："},
    "web": {"use_rag": False, "use_search": True, "prefix": "请联网搜索："},
    "db": {"use_rag": False, "use_search": False, "prefix": "请查数据库："},
    "code": {"use_rag": False, "use_search": False, "prefix": "请用代码计算："},
    "default": {"use_rag": True, "use_search": True, "prefix": ""},
}


class _HostExecutor:
    """把每步动作委托给主应用 Supervisor（run_agent）。"""

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        route = _SOURCE_ROUTE.get(request.source) or _SOURCE_ROUTE["default"]
        question = (route["prefix"] + request.action) if route["prefix"] else request.action
        result = await run_agent(
            question=question,
            use_rag=route["use_rag"],
            use_search=route["use_search"],
            use_memory=False,
            session_id=f"sub-{uuid.uuid4().hex[:8]}",
            user_id="default",
            resume=None,
            checkpoint_id=None,
            on_event=None,
        )
        return StepResult(answer=result.get("answer", "") or "（子任务无输出）")


# 图缓存：键含运行配置与 checkpointer 就绪状态，避免配置变化后继续使用过期图。
# LLM 无需入键：llm_factory 每次调用实时走 get_llm（模型切换后自动生效）。
_host_agent_cache: dict[tuple, Any] = {}


def build_host_task_agent() -> Any:
    """构建宿主版自主任务 Agent（按配置缓存）。"""
    key = (
        settings.task_agent_mode,
        settings.task_agent_hitl,
        settings.task_agent_max_retries,
        settings.llm_timeout,
        settings.llm_max_retries,
        get_checkpointer() is not None,
    )
    if key in _host_agent_cache:
        return _host_agent_cache[key]
    config = TaskAgentConfig(
        mode=settings.task_agent_mode,
        hitl=settings.task_agent_hitl,
        max_retries=settings.task_agent_max_retries,
        llm_timeout=settings.llm_timeout,
        llm_max_retries=settings.llm_max_retries,
    )
    agent = build_agent(
        config=config,
        llm_factory=lambda: get_llm("light"),
        checkpointer_provider=get_checkpointer,
        executor=_HostExecutor(),
    )
    _host_agent_cache[key] = agent
    return agent
