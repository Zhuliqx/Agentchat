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
from app.agents.middleware import resilience_middleware
from app.agents.prompts import build_supervisor_prompt
from app.agents.streaming import _PreludeDedupe
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
    get_recent_rag_sources,
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

    graph = create_agent(
        get_llm(),
        tools=tools,
        system_prompt=build_supervisor_prompt(use_rag, use_search, use_memory),
        checkpointer=get_checkpointer(),
        store=get_store(),
        context_schema=UserContext,
        # 容错：统一模型调用超时/重试/日志（middleware）
        middleware=[resilience_middleware()],
        # 缓存：相同输入命中，跳过重复 LLM 调用（受开关控制，见 config.py）
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
    """run_agent / stream_agent 公共准备：构建图、组装输入与 config。

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


def _analyze_result(msgs: list) -> tuple[list[str], list[str], str]:
    """从最终消息列表提取 used_agents / tool_calls / 最终答案。"""
    used_agents: list[str] = []
    tool_calls_log: list[str] = []
    for m in msgs:
        if getattr(m, "type", "") == "tool":
            name = getattr(m, "name", "")
            if name in AGENT_TOOL_NAMES:
                used_agents.append(name)
            tool_calls_log.append(name)
    # 最终答案：逆序找最后一条 AI 消息（避免工具末轮取到原始输出）
    return list(dict.fromkeys(used_agents)), list(dict.fromkeys(tool_calls_log)), last_ai_text(msgs)


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
    """运行多 Agent 编排，返回答案与执行信息。

    - session_id: 作为 Checkpointer 的 thread_id，实现会话状态持久化。
    - user_id: 长期记忆归属用户。
    - resume: 若非空，表示这是 HITL 恢复调用——用 Command(resume=...) 从
      上次 interrupt 处继续（同一 thread_id），不再重新传入问题。
    - checkpoint_id: Time Travel——非空时从指定历史 checkpoint 继续（分叉新分支）。
    - on_event: 可选回调，收到 {"type": ..., "content": ...} 事件。
    """
    graph, input_data, config = _prepare_run(
        question, resume, session_id, use_rag, use_search, use_memory, checkpoint_id
    )

    if on_event:
        await on_event({"type": "start", "content": "Supervisor 开始调度..."})

    async with _agent_timeout_scope(on_event):
        result = await graph.ainvoke(
            input_data,
            config=config,
            context=UserContext(user_id=user_id or "default", session_id=session_id or ""),
        )
    msgs = result["messages"]

    hitl_pending = _extract_hitl(result)
    used_agents, tool_calls_log, answer = _analyze_result(msgs)

    await _emit_final_events(on_event, used_agents, tool_calls_log, hitl_pending)

    return {
        "answer": answer,
        "used_agents": used_agents,
        "tool_calls": tool_calls_log,
        "message_count": len(msgs),
        "hitl_pending": hitl_pending,
    }


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

    answer_parts: list[str] = []
    used_agents: list[str] = []
    tool_calls_log: list[str] = []
    hitl_pending = None
    # 开场白缓冲：工具触发前不逐字显示，超过阈值判定为直接回答后平滑流式；
    # prelude_total 保留全部开场白，供工具后去重（LLM 常连同答案重新生成）。
    PRELUDE_FLUSH = 40  # 超过该长度判定为直接回答，开始逐字流式
    prelude_total: list[str] = []  # 全部开场白（含已流式部分），供工具后去重
    prelude_buf: list[str] = []    # 尚未判定是否直接回答的缓冲文本
    streaming_direct = False       # 已判定为直接回答 → 后续逐字流式
    saw_tool_call = False
    dedupe: _PreludeDedupe | None = None  # 工具后答案流式去重（跳过重复开场白前缀）
    pending_tool_name: str | None = None  # 最近已发出 tool 事件的工具名（避免重复）
    # 本次实际注册的工具名集合（按开关）：过滤模型幻觉调用的未注册工具，
    # 避免误发 phantom tool 事件让用户误以为真的联网了。
    registered_tools: set[str] = {"mcp_agent"}
    if use_rag:
        registered_tools.add("rag_agent")
    if use_search:
        registered_tools.add("web_search")
    if use_memory:
        registered_tools.add("remember_memory")
        registered_tools.add("recall_memory")
    if settings.code_agent_enabled:
        registered_tools.add("code_agent")

    async def _push(text: str) -> None:
        """推送一段文本到答案流（并记录到 answer_parts）。"""
        answer_parts.append(text)
        if on_token:
            await on_token(text)

    async def _emit_tool(name: str) -> None:
        """统一处理工具调用：丢弃未流式的开场白碎片并发出 tool 事件。

        工具调用前的短开场白（如“我来帮您”）尚未流式时直接丢弃 prelude_buf，
        避免碎片感；若已判定为直接回答（streaming_direct）则开场白已流式，无需
        处理。仅用 prelude_total 记录完整开场白供工具后去重。仅对本次实际注册的
        工具（registered_tools）生效——模型可能幻觉吐出未注册工具（如开关关闭
        时的 web_search）的 tool_call chunk，这类不应点亮工具轨道，也不应计入
        saw_tool_call（否则会干扰后续去重与开场白逻辑）。
        """
        nonlocal saw_tool_call, dedupe, pending_tool_name
        is_real = name in registered_tools
        if is_real and not saw_tool_call:
            if not streaming_direct:
                prelude_buf.clear()  # 丢弃未显示的开场白碎片
            dedupe = _PreludeDedupe("".join(prelude_total))
            saw_tool_call = True
        if on_event and is_real and name != pending_tool_name:
            pending_tool_name = name
            data: dict = {}
            # 引用溯源：rag_agent 执行后附带检索命中的文档来源
            if name == "rag_agent":
                data["sources"] = get_recent_rag_sources(user_id or "default")
            await on_event({"type": "tool", "content": f"工具: {name}", "data": data})

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
                        # 流式返回 tool_call_chunks），_emit_tool 会补推开场白
                        await _emit_tool(name)
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
                    # _emit_tool 丢弃（不显示碎片）；若已判定为直接回答则已流式
                    if not saw_tool_call and text:
                        prelude_total.append(text)
                        if streaming_direct:
                            await _push(text)
                        else:
                            prelude_buf.append(text)
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
                    await _emit_tool(name)
                elif saw_tool_call:
                    # 工具已调用：后续为最终答案。LLM 在工具后常重新生成完整回答
                    # （重复了开场白）→ 流式前缀匹配，跳过重复的开场白前缀
                    if dedupe is not None and dedupe.active:
                        text = dedupe.feed(text)
                    if text:
                        await _push(text)
                else:
                    # 工具调用前（或直接回答）：未判定时缓冲；超过阈值即判定为直接
                    # 回答并开始逐字流式（此后不再攒段，保证平滑不卡顿）
                    if text:
                        prelude_total.append(text)
                        if streaming_direct:
                            await _push(text)
                        else:
                            prelude_buf.append(text)
                            if len("".join(prelude_buf)) >= PRELUDE_FLUSH:
                                streaming_direct = True
                                await _push("".join(prelude_buf))
                                prelude_buf.clear()

        # 流结束：尚未判定的短文本（<阈值）补推（例如很短的直接回答）
        if prelude_buf:
            await _push("".join(prelude_buf))
            prelude_buf.clear()

    await _emit_final_events(
        on_event, list(dict.fromkeys(used_agents)), [], hitl_pending
    )

    return {
        "answer": "".join(answer_parts),
        "used_agents": list(dict.fromkeys(used_agents)),
        "tool_calls": list(dict.fromkeys(tool_calls_log)),
        "hitl_pending": hitl_pending,
    }
