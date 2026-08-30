"""宿主适配器：把主应用能力注入独立 task_agent 包。

- LLM        ：get_llm("light")（每次调用实时解析，模型切换后自动生效）；
- Checkpointer：get_checkpointer()（None 时包内自动降级无状态）；
- Executor    ：包装 run_agent，按 source 收紧 use_rag/use_search（沿用原 _SOURCE_ROUTE），
  每个执行步使用独立 thread（sub-<uuid>），与旧行为一致。
- 跨任务记忆 ：get_store() 就绪时注入 _HostMemory（namespace=(user, "task_memories")），
  任务开始召回历史结论、结束沉淀 final_answer；Store 不可用时自动降级无记忆。
- 事件回调   ：build_host_task_agent(on_event=...) 把执行过程事件（plan/replan/execute/check/final…）
  透传给调用方（路由可接 SSE），传入 sink 时绕过图缓存以隔离每次请求。
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
from app.db.memory_store import get_checkpointer, get_store

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


class _HostMemory:
    """跨任务记忆：宿主 Postgres Store(namespace=(user_id, "task_memories"))。

    实现 task_agent.memory.TaskMemory；Store 未初始化 / 检索失败时安全降级为空。
    """

    def __init__(self, user_id: str = "default") -> None:
        self._namespace = (user_id, "task_memories")

    async def recall(self, goal: str) -> list[str]:
        store = get_store()
        if store is None:
            return []
        try:
            items = await store.asearch(self._namespace, query=goal, limit=10)
            return [str(i.value.get("summary") or "") for i in items if i.value][:5]
        except Exception:  # noqa: BLE001 - 无语义索引/检索失败 → 降级全量扫描
            pass
        try:
            items = await store.alist(self._namespace, limit=100)
        except Exception:  # noqa: BLE001
            return []
        hits = []
        for i in items:
            summary = str((i.value or {}).get("summary") or "")
            if summary and _token_overlap(goal, summary):
                hits.append(summary)
        return hits[:5]

    async def remember(self, goal: str, summary: str) -> None:
        store = get_store()
        if store is None:
            return
        try:
            await store.aput(
                self._namespace,
                key=goal,  # 同目标覆盖，避免重复累积
                value={"goal": goal, "summary": summary},
            )
        except Exception:  # noqa: BLE001 - 记忆写入失败不影响交付
            pass


def _token_overlap(goal: str, text: str) -> bool:
    """简单召回判定：goal 的 ≥2 字片段在 text 中出现即视为相关。"""
    goal_c = "".join(ch for ch in goal if "\u4e00" <= ch <= "\u9fff")
    for i in range(len(goal_c) - 1):
        if goal_c[i : i + 2] in text:
            return True
    return False


# 图缓存：键含运行配置与 checkpointer 就绪状态，避免配置变化后继续使用过期图。
# LLM 无需入键：llm_factory 每次调用实时走 get_llm（模型切换后自动生效）；
# memory 就绪状态入键：Store 初始化与否影响注入。
_host_agent_cache: dict[tuple, Any] = {}


def build_host_task_agent(
    on_event: Any | None = None,
) -> Any:
    """构建宿主版自主任务 Agent（按配置缓存）。

    on_event：可选 (kind, data) 事件回调（见 task_agent 事件流）；传入时绕过缓存，
    以隔离每次请求的 sink（事件不跨请求共享）。
    """
    key = (
        settings.task_agent_mode,
        settings.task_agent_hitl,
        settings.task_agent_max_retries,
        settings.llm_timeout,
        settings.llm_max_retries,
        get_checkpointer() is not None,
        get_store() is not None,
    )
    if on_event is None and key in _host_agent_cache:
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
        on_event=on_event,
        memory=_HostMemory("default") if get_store() is not None else None,
    )
    if on_event is None:
        _host_agent_cache[key] = agent
    return agent
