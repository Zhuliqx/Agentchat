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

import json
import logging
import uuid
from typing import Any

from task_agent.config import TaskAgentConfig
from task_agent.executor import ExecuteRequest, StepResult
from task_agent.graph import build_agent, list_task_history  # noqa: F401 - 供路由统一导入

try:
    from task_agent.memory import tokenize  # agentchat-task-agent >= 0.1.3
except ImportError:  # 兼容已发布的 0.1.2（公开 tokenize 尚未发布）：回退旧私有 _tokens
    from task_agent.memory import _tokens as tokenize  # type: ignore[no-redef]

from langchain_core.messages import HumanMessage

from app.agents.tools.text import extract_text
from app.agents.graph import run_agent
from app.agents.llm import get_llm
from app.config import settings
from app.db.memory_store import (
    get_checkpointer,
    get_store,
    safe_asearch,
    store_has_index,
)

logger = logging.getLogger(__name__)

# 宿主侧路由表：replan 标注的信息来源 → 执行层收紧开关 + 前缀引导
_SOURCE_ROUTE: dict[str, dict] = {
    "kb": {"use_rag": True, "use_search": False, "prefix": "请用知识库查询："},
    "web": {"use_rag": False, "use_search": True, "prefix": "请联网搜索："},
    "db": {"use_rag": False, "use_search": False, "prefix": "请查数据库："},
    "code": {"use_rag": False, "use_search": False, "prefix": "请用代码计算："},
    "default": {"use_rag": True, "use_search": True, "prefix": ""},
}


class LangChainLLM:
    """把宿主 LangChain ChatModel 适配为 task_agent.LLM 协议。

    BaseChatModel.ainvoke 参数名为 `input`（运行时接受 str，但类型不兼容 LLM 协议），
    这里显式包装并转成 HumanMessage，保证类型与运行时语义一致。

    同时实现可选的 task_agent.JSONLLM 能力（``ainvoke_json``）：按 provider 能力
    依次尝试 JSON Schema 结构化输出（OpenAI 系）→ 通用 JSON mode
    （DeepSeek/DashScope/OpenAI 兼容端点用 response_format=json_object，
    Ollama 用 format=json）；任何失败返回 None，由引擎层重试并降级到
    「自由文本 + 容错解析」。
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    async def ainvoke(self, prompt: str) -> Any:
        return await self._model.ainvoke([HumanMessage(content=prompt)])

    async def ainvoke_json(
        self, prompt: str, schema: dict | None = None
    ) -> dict | None:
        model = self._model
        # 1) 显式 schema → 结构化输出（OpenAI 系原生；失败静默降级 JSON mode）
        if schema is not None:
            try:
                structured = model.with_structured_output(schema)
                resp = await structured.ainvoke([HumanMessage(content=prompt)])
                if isinstance(resp, dict):
                    return resp
            except Exception:  # noqa: BLE001 - provider 不支持 → 继续降级
                pass
        # 2) 通用 JSON mode（DeepSeek/DashScope/OpenAI 兼容端点 / Ollama）
        try:
            from langchain_ollama import ChatOllama

            is_ollama = isinstance(model, ChatOllama)
        except ImportError:  # langchain_ollama 未安装 → 按 OpenAI 兼容处理
            is_ollama = False
        try:
            bound = (
                model.bind(format="json")
                if is_ollama
                else model.bind(response_format={"type": "json_object"})
            )
            resp = await bound.ainvoke([HumanMessage(content=prompt)])
            text = extract_text(getattr(resp, "content", "")).strip()
            if not text:
                return None
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 - 结构化失败交给引擎降级
            logger.warning("LLM JSON 输出失败，降级文本解析: %s", exc)
            return None
class _HostExecutor:
    """把每步动作委托给主应用 Supervisor（run_agent）。"""

    async def __call__(self, request: ExecuteRequest) -> StepResult:
        route = _SOURCE_ROUTE.get(request.source) or _SOURCE_ROUTE["default"]
        question = (route["prefix"] + request.action) if route["prefix"] else request.action
        for _attempt in range(2):  # 偶发空响应 → 重试一次（不重复计步，见 task-agent verify 语义）
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
            if result.get("hitl_pending") is not None:
                pending = result["hitl_pending"]
                pending_q = (
                    str(pending.get("question") or "")
                    if isinstance(pending, dict)
                    else ""
                )
                return StepResult(answer=f"（等待人工确认：{pending_q}）")
            answer = result.get("answer", "") or ""
            if answer.strip():
                return StepResult(answer=answer)
        return StepResult(answer="（子任务无输出）")


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
        # 语义检索仅在 Store 有索引时尝试（无索引是常态降级，不必发起已知失败的调用）；
        # 成功（含空结果）直接返回；失败才降级全量扫描。
        if store_has_index():
            items = await safe_asearch(store, self._namespace, query=goal, limit=10)
            if items is not None:
                return [
                    str(i.value.get("summary") or "") for i in items if i.value
                ][:5]
        # 无索引 / 语义失败 → 列出该 namespace 全部，再做关键词重叠过滤
        items = await safe_asearch(store, self._namespace, limit=100)
        if items is None:
            return []
        hits = []
        for i in items:
            summary = str((i.value or {}).get("summary") or "")
            if summary and tokenize(goal) & tokenize(summary):
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
        settings.task_agent_max_steps,
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
        max_steps=settings.task_agent_max_steps,
        llm_timeout=settings.llm_timeout,
        llm_max_retries=settings.llm_max_retries,
    )
    agent = build_agent(
        config=config,
        llm_factory=lambda: LangChainLLM(get_llm("light")),
        checkpointer_provider=get_checkpointer,
        executor=_HostExecutor(),
        on_event=on_event,
        memory=_HostMemory("default") if get_store() is not None else None,
    )
    if on_event is None:
        _host_agent_cache[key] = agent
    return agent
