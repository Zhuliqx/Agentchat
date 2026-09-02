"""LangGraph 多 Agent 编排（Supervisor 层级模式）。

结构:
                +------------+
                | supervisor |  (LLM，决定调用哪个子 Agent 或直接回答)
                +-----+------+
                      |
        +-------------+-------------+
        |             |             |
    rag_agent      mcp_agent    web_search
   (检索+生成)   (MCP 工具)   (Tavily 联网搜索)

记忆能力（LangGraph 原生机制）：
- 短期记忆：Checkpointer（thread_id=session_id）持久化图状态
- 运行时上下文：context_schema=UserContext + context= 传入，工具经 Runtime 访问
- 长期记忆：Store（AsyncPostgresStore）+ remember/recall 工具（namespace 隔离）
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from langchain_core.caches import InMemoryCache
from langchain_core.messages import AIMessageChunk
from langgraph.types import Command

logger = logging.getLogger(__name__)

from app.agents.context import UserContext
from app.agents.llm import get_llm
from app.agents.middleware import build_supervisor_middlewares
from app.agents.prompts import build_supervisor_prompt
from app.agents.streaming import SupervisorStreamer
from app.agents.tools import (
    agent_to_tool,
    build_code_agent,
    build_confirmation_tool,
    build_mcp_agent,
    build_rag_agent,
    build_recall_tool,
    build_remember_tool,
    build_search_tool,
    extract_text,
    last_ai_text,
)
from app.config import settings
from app.db.memory_store import get_checkpointer, get_store


class AgentTimeoutError(Exception):
    """单轮对话执行超时。"""


# 参与 used_agents 统计的 Agent 工具名（web_search 为直接工具；code_agent 有意不统计）
AGENT_TOOL_NAMES = ("rag_agent", "mcp_agent", "web_search")

# 缓存不同配置下的图
_graph_cache: dict[tuple, Any] = {}


def clear_graph_cache() -> None:
    """清除图缓存。运行时切换模型后调用，使下次请求按新模型重建 Supervisor 图。"""
    _graph_cache.clear()

# 图执行/LLM 提示缓存单例（受 AGENT_CACHE_ENABLED 控制；相同输入命中跳过重复 LLM 调用）
_agent_cache: Any = None


def _get_agent_cache() -> Any:
    """返回共享缓存实例（幂等；关闭时返回 None）。

    返回类型用 Any：langchain_core 的 BaseCache 与 create_agent 的类型标注存在
    泛型分歧（Pylance 报告不一致），而 InMemoryCache 运行时确为 BaseCache 子类。
    """
    global _agent_cache
    if _agent_cache is None and settings.agent_cache_enabled:
        _agent_cache = InMemoryCache()
    return _agent_cache


def get_supervisor_graph(
    use_rag: bool = True, use_search: bool = True, use_memory: bool = True
) -> Any:
    """构建（并缓存）Supervisor 图。可分别开关 RAG / 联网搜索 / 长期记忆。"""
    # 配置指纹含 checkpointer/store 就绪状态，避免状态变化后继续使用过期图
    key = (
        use_rag,
        use_search,
        use_memory,
        get_checkpointer() is not None,
        get_store() is not None,
        settings.history_summary_enabled,
        settings.history_summary_trigger_tokens,
        settings.history_summary_min_messages,
        settings.history_summary_keep_messages,
        settings.history_summary_max_input_tokens,
        settings.agent_max_tool_calls,
        settings.agent_max_model_calls,
    )
    if key in _graph_cache:
        return _graph_cache[key]

    from langchain.agents import create_agent

    # HITL：有开关控制的动作（rag/search/remember）视为已授权豁免；
    # 仅无开关的外部操作（如 mcp）按 hitl_actions 逐次确认。
    _switch_open = {
        "rag": use_rag,
        "search": use_search,
        "remember": use_memory,
    }

    def _needs_confirm(action: str) -> bool:
        if not settings.hitl_enabled:
            return False
        if _switch_open.get(action):
            return False  # 有开关且已打开 = 已授权，豁免
        return action in settings.hitl_actions

    tools = []
    if use_rag:
        rag_agent = build_rag_agent()
        tools.append(
            agent_to_tool(
                rag_agent,
                "rag_agent",
                "知识库问答：检索已摄入文档并回答问题。问题涉及文档资料时使用。",
                confirm_before=_needs_confirm("rag"),
            )
        )

    mcp_agent = build_mcp_agent()
    tools.append(
        agent_to_tool(
            mcp_agent,
            "mcp_agent",
            "工具调用：数据库查询、时间计算、外部 MCP 工具。问题需要查库或调工具时使用。",
            confirm_before=_needs_confirm("mcp"),
        )
    )

    if use_search:
        search_tool = build_search_tool(confirm_before=_needs_confirm("search"))
        if search_tool is not None:
            tools.append(search_tool)

    # 代码 Agent（受限沙箱执行 Python；受 CODE_AGENT_ENABLED 控制）
    if settings.code_agent_enabled:
        tools.append(
            agent_to_tool(
                build_code_agent(),
                "code_agent",
                "代码执行与计算：运行 Python 脚本、验证算法、数学计算、数据处理。问题需要实际计算或执行代码时使用。",
            )
        )

    # 长期记忆工具（use_memory 关闭时不注册）
    if use_memory:
        tools.append(build_remember_tool())
        tools.append(build_recall_tool())
    # 人工确认工具（HITL）：仅在未配置自动确认动作时提供软性确认工具。
    # 若 hitl_actions 已配置（如 search），对应动作由 confirm_before 强制确认，
    # 不注册本工具以避免 supervisor 重复请求确认（双重确认）。
    if settings.hitl_enabled and not settings.hitl_actions:
        tools.append(build_confirmation_tool())

    # 摘要压缩 / 调用上限 / 超时日志统一在 middleware 工厂组装（返回 list[Any]，
    # 避免各中间件泛型 StateT/ContextT 差异触发 create_agent 类型推断冲突）。
    middleware = build_supervisor_middlewares(history_summary_model=get_llm("light"))

    graph = create_agent(
        get_llm(),
        tools=tools,
        system_prompt=build_supervisor_prompt(use_rag, use_search, use_memory),
        checkpointer=get_checkpointer(),
        store=get_store(),
        context_schema=UserContext,
        # 容错：统一模型调用超时/重试/日志（middleware）
        middleware=middleware,
        # 缓存：相同输入命中，跳过重复 LLM 调用
        cache=_get_agent_cache(),
    )
    _graph_cache[key] = graph
    return graph


def _prepare_run(
    question: str,
    resume: str | None,
    session_id: str | None,
    use_rag: bool,
    use_search: bool,
    use_memory: bool = True,
    checkpoint_id: str | None = None,
) -> tuple[Any, Any, dict | None]:
    """stream_agent 准备：构建图、组装输入与 config。

    返回 (graph, input_data, config)。resume 非空时输入为 Command(resume)；
    否则只传当前消息——历史由 Checkpointer 从 thread_id（或 checkpoint_id）自动恢复。
    checkpoint_id 非空时为 Time Travel：config 带上该历史 checkpoint，从该点
    继续（分叉一条新分支）。
    """
    graph = get_supervisor_graph(
        use_rag=use_rag, use_search=use_search, use_memory=use_memory
    )
    cp = get_checkpointer()
    config: dict[str, Any] | None = (
        {"configurable": {"thread_id": session_id}} if (cp and session_id) else None
    )
    if config and checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    # 可观测性：挂 Langfuse handler（未配置则 None，fail-open）。
    # 放在每次 invocation 的 config 而非 lru 缓存的图实例，避免跨会话复用。
    from app.observability import get_langfuse_handler

    lf_handler = get_langfuse_handler()
    if config and lf_handler is not None:
        config["callbacks"] = [lf_handler]
    if resume is not None:
        input_data: Any = Command(resume=resume)
    else:
        input_data = {"messages": [("user", question)]}
    return graph, input_data, config


async def list_checkpoint_history(
    session_id: str, limit: int = 30
) -> list[dict]:
    """Time Travel：列出线程的 checkpoint 历史（新→旧），供前端时间线/回退。

    每条包含 checkpoint_id（可用于 replay/fork）、parent_checkpoint_id、
    创建时间、下一步节点、最后一条 AI 消息摘要、是否处于中断等待确认状态。
    """
    cp = get_checkpointer()
    if cp is None or not session_id:
        return []
    graph = get_supervisor_graph(use_rag=True, use_search=True)
    result: list[dict] = []
    try:
        async for snap in graph.aget_state_history(
            {"configurable": {"thread_id": session_id}}
        ):
            cfg = (snap.config or {}).get("configurable", {}) or {}
            parent_cfg = (snap.parent_config or {}).get("configurable", {}) or {}
            msgs = snap.values.get("messages") or []
            # 摘要：最后一条 AI 消息的文本（截断展示）
            summary = last_ai_text(msgs)
            created = getattr(snap, "created_at", None)
            iso = None
            if created is not None and hasattr(created, "isoformat"):
                iso = created.isoformat()
            result.append(
                {
                    "checkpoint_id": cfg.get("checkpoint_id"),
                    "parent_checkpoint_id": parent_cfg.get("checkpoint_id"),
                    "created_at": iso,
                    "next": list(snap.next) if snap.next else [],
                    "summary": summary[:150],
                    "task_count": len(getattr(snap, "tasks", []) or []),
                    "interrupted": any(
                        getattr(t, "interrupts", None)
                        for t in (getattr(snap, "tasks", []) or [])
                    ),
                }
            )
            if len(result) >= limit:
                break
    except Exception as exc:
        logger.warning("读取 checkpoint 历史失败: %s", exc)
        return []
    return result


def _extract_hitl(result: dict) -> Any:
    """从图执行结果提取 HITL 待确认内容（无则 None）。"""
    interrupts = result.get("__interrupt__")
    return getattr(interrupts[0], "value", None) if interrupts else None


async def _emit_final_events(
    on_event: Callable[[dict], Awaitable[None]] | None,
    used_agents: list[str],
    tool_calls_log: list[str],
    hitl_pending: Any,
) -> None:
    """统一按 agent -> tool -> interrupt(或 end) 顺序发出结果事件。

    流式调用中 tool 事件已在执行过程中实时推送，此时传空 tool_calls_log 即可。
    """
    if not on_event:
        return
    for name in used_agents:
        await on_event({"type": "agent", "content": f"调用 {name}"})
    for name in tool_calls_log:
        await on_event({"type": "tool", "content": f"工具: {name}"})
    if hitl_pending is not None:
        await on_event(
            {
                "type": "interrupt",
                "content": hitl_pending.get("question", ""),
                "data": hitl_pending,
            }
        )
    else:
        await on_event({"type": "end", "content": "编排完成"})


@asynccontextmanager
async def _agent_timeout_scope(on_event: Callable[[dict], Awaitable[None]] | None):
    """统一 agent 执行超时处理：超时发 error 事件并抛 AgentTimeoutError。"""
    try:
        async with asyncio.timeout(settings.agent_timeout):
            yield
    except TimeoutError as exc:
        if on_event:
            await on_event({"type": "error", "content": "处理超时，请重试或简化问题"})
        raise AgentTimeoutError("Agent 执行超时") from exc


async def run_agent(
    question: str,
    use_rag: bool = True,
    use_search: bool = True,
    use_memory: bool = True,
    session_id: str | None = None,
    user_id: str = "default",
    resume: str | None = None,
    checkpoint_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """非流式运行多 Agent：`stream_agent` 的薄封装。

    同一张图、同一套超时 / HITL / Time Travel 语义；答案由 stream_agent
    拼接（工具后自动去重重复开场白），事件经 on_event 收集。保留此入口
    供非流式接口 / 子任务执行器使用，避免调用方各自组装收集逻辑。
    """
    return await stream_agent(
        question=question,
        use_rag=use_rag,
        use_search=use_search,
        use_memory=use_memory,
        session_id=session_id,
        user_id=user_id,
        resume=resume,
        checkpoint_id=checkpoint_id,
        on_event=on_event,
    )


async def stream_agent(
    question: str,
    use_rag: bool = True,
    use_search: bool = True,
    use_memory: bool = True,
    session_id: str | None = None,
    user_id: str = "default",
    resume: str | None = None,
    checkpoint_id: str | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Token 级流式运行多 Agent（SSE 用）。

    - on_event: 事件回调（start / agent / tool / end / interrupt / error）。
    - on_token: 收到 supervisor 输出的增量 token 文本（逐 token 推送）。
    - resume: 若非空，表示 HITL 恢复——用 Command(resume=...) 从上次
      interrupt 处继续（同一 thread_id），不重新传入问题。
    - checkpoint_id: Time Travel——非空时从指定历史 checkpoint 继续（分叉新分支）。
    仅流式顶层 supervisor（`model` 节点、checkpoint_ns 不含 "|"）的 AI 文本，
    子 Agent 的中间输出不推送，避免混乱。
    """
    graph, input_data, config = _prepare_run(
        question, resume, session_id, use_rag, use_search, use_memory, checkpoint_id
    )

    if on_event:
        await on_event({"type": "start", "content": "Supervisor 开始调度..."})

    used_agents: list[str] = []
    tool_calls_log: list[str] = []
    hitl_pending = None
    # 输出装配（开场白缓冲 / 工具事件 / 工具后去重）抽到 SupervisorStreamer
    streamer = SupervisorStreamer(
        on_token=on_token, on_tool_event=on_event, user_id=user_id
    )
    if use_rag:
        streamer.register_tool("rag_agent")
    if use_search:
        streamer.register_tool("web_search")
    if use_memory:
        streamer.register_tool("remember_memory")
        streamer.register_tool("recall_memory")
    if settings.code_agent_enabled:
        streamer.register_tool("code_agent")

    async with _agent_timeout_scope(on_event):
        async for mode, data in graph.astream(
            input_data,
            config=config,
            context=UserContext(user_id=user_id or "default", session_id=session_id or ""),
            stream_mode=["updates", "messages"],
        ):
            if mode == "updates":
                for node, update in data.items():
                    # HITL 中断：特殊节点 __interrupt__ 携带待确认内容
                    if node == "__interrupt__":
                        hits = (
                            update
                            if isinstance(update, (list, tuple))
                            else [update]
                        )
                        if hits:
                            hitl_pending = getattr(hits[0], "value", None)
                        continue
                    if not (isinstance(update, dict) and "messages" in update):
                        continue
                    for m in update["messages"]:
                        mtype = getattr(m, "type", "")
                        if mtype != "tool":
                            continue
                        name: str = str(getattr(m, "name", "") or "")
                        if name in AGENT_TOOL_NAMES:
                            used_agents.append(name)
                        tool_calls_log.append(name)
                        # 兜底：若未在流式 token 中检测到 tool_call（个别模型不
                        # 流式返回 tool_call_chunks），streamer.emit_tool 会补推开场白
                        await streamer.emit_tool(name)
            elif mode == "messages":
                chunk, meta = data
                # 只流式顶层 supervisor（model 节点）的 AI 文本 token。
                # checkpoint_ns 用 "|" 分隔嵌套任务：顶层形如 "model:<task_id>"，
                # 子 Agent 形如 "model:<id>|mcp_agent:<id>"（含 "|"），跳过后者。
                if "|" in (meta.get("langgraph_checkpoint_ns") or ""):
                    continue  # 子 Agent 的嵌套命名空间
                if not isinstance(chunk, AIMessageChunk):
                    continue  # 只取 AI 生成文本，排除工具结果
                # 检测工具调用：AIMessageChunk 携带 tool_call_chunks 说明本
                # LLM 调用即将执行工具（工具执行前，token 流已给出完整开场白）
                tool_chunks = getattr(chunk, "tool_call_chunks", None) or []
                has_tool = any(
                    isinstance(tc, dict) and tc.get("name") for tc in tool_chunks
                )
                text = extract_text(getattr(chunk, "content", ""))
                if has_tool:
                    # 工具即将执行：同 chunk 的 content 属于开场白，缓冲后由
                    # emit_tool 丢弃（不显示碎片）；若已判定为直接回答则已流式
                    if not streamer.saw_tool_call and text:
                        await streamer.record_tool_prelude(text)
                    name: str = str(
                        next(
                            (
                                tc.get("name")
                                for tc in tool_chunks
                                if isinstance(tc, dict) and tc.get("name")
                            ),
                            "",
                        )
                        or ""
                    )
                    await streamer.emit_tool(name)
                elif streamer.saw_tool_call:
                    # 工具已调用：后续为最终答案。LLM 在工具后常重新生成完整回答
                    # （重复了开场白）→ 流式前缀匹配，跳过重复的开场白前缀
                    await streamer.feed_answer(text)
                else:
                    # 工具调用前（或直接回答）：未判定时缓冲；超过阈值即判定为直接
                    # 回答并开始逐字流式（此后不再攒段，保证平滑不卡顿）
                    if text:
                        await streamer.feed(text)

        # 流结束：尚未判定的短文本（<阈值）补推（例如很短的直接回答）
        await streamer.flush()

    await _emit_final_events(
        on_event, list(dict.fromkeys(used_agents)), [], hitl_pending
    )

    return {
        "answer": streamer.answer(),
        "used_agents": list(dict.fromkeys(used_agents)),
        "tool_calls": list(dict.fromkeys(tool_calls_log)),
        "hitl_pending": hitl_pending,
    }
