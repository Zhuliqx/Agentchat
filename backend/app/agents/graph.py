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

记忆能力（LangGraph 官方机制）：
- 短期记忆：Checkpointer（thread_id=session_id）持久化图状态
- 运行时上下文：context_schema=UserContext + context= 传入，工具经 Runtime 访问
- 长期记忆：Store（AsyncPostgresStore）+ remember/recall 工具（namespace 隔离）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from langchain_core.caches import InMemoryCache
from langchain_core.messages import AIMessageChunk
from langgraph.types import Command

logger = logging.getLogger(__name__)

from app.agents.context import UserContext
from app.agents.llm import get_llm
from app.agents.middleware import resilience_middleware
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
)
from app.config import settings
from app.db.memory_store import get_checkpointer, get_store


class AgentTimeoutError(Exception):
    """单轮对话执行超时。"""


def _build_supervisor_prompt(
    use_rag: bool = True, use_search: bool = True, use_memory: bool = True
) -> str:
    """按开关动态生成 supervisor 提示词。

    关闭知识库/搜索/记忆时，对应工具不会注册进图；提示词也必须**同步移除**对应
    描述并明确禁止，否则 LLM 会幻觉调用不存在的工具（生成 name=rag_agent 的
    工具消息，事件流误报"工具: rag_agent"，且回答谎称"查了知识库"）。
    """
    tool_lines = []
    if use_rag:
        tool_lines.append(
            "- rag_agent：知识库问答。当问题涉及已摄入的文档/资料/公司信息/产品信息时使用。"
        )
    tool_lines.append(
        "- mcp_agent：数据库查询、时间计算等 MCP 工具。当问题需要查数据库或调用外部工具时使用。"
    )
    if settings.code_agent_enabled:
        tool_lines.append(
            "- code_agent：执行 Python 代码并返回结果。当需要实际计算、验证算法、数学计算、数据处理或运行脚本时使用。"
        )
    if use_search:
        tool_lines.append(
            "- web_search：直接联网搜索最新网络信息（Tavily）。当问题需要实时/最新资讯时使用；"
            "调用时传入提炼好的 1-3 个搜索关键词。"
        )
    if use_memory:
        tool_lines.append(
            "- remember_memory：把用户透露的重要信息/偏好保存到长期记忆（跨会话有效）。"
        )
        tool_lines.append(
            "- recall_memory：读取该用户的长期记忆（背景、偏好、历史信息）。"
        )
    if settings.hitl_enabled and not settings.hitl_actions:
        tool_lines.append(
            "- request_confirmation：请求用户确认/授权。**仅当操作没有对应开关且风险较高**"
            "（数据库写入、外部 MCP 调用、不可逆操作等），或用户明确要求确认时，调用本工具"
            "征得用户同意；用户已开启开关的能力（联网/知识库/记忆）无需调用。"
        )

    rules = []
    rn = 1
    if use_rag:
        rules.append(
            f"{rn}. 知识库问题（文档、资料、公司介绍、产品信息等）**必须**调用 rag_agent，"
            "绝不要用 mcp_agent 去查数据库代替。"
        )
    else:
        rules.append(
            f"{rn}. **知识库检索已关闭**：禁止调用 rag_agent，也不要声称查询/引用了知识库内容，"
            "不要编造文档/资料信息。"
        )
    rn += 1
    rules.append(f"{rn}. 需要查数据库/统计/时间/计算 -> 调用 mcp_agent。")
    rn += 1
    if settings.code_agent_enabled:
        rules.append(
            f"{rn}. 需要实际计算/执行代码/验证算法/数学计算/数据处理 -> 调用 code_agent。"
        )
        rn += 1
    if use_search:
        rules.append(f"{rn}. 需要实时资讯/最新信息/新闻 -> 调用 web_search。")
        rn += 1
    if use_memory:
        rules.append(
            f"{rn}. 用户提供个人信息、偏好，或说\"记住/我叫/我的名字/我喜欢\"时，"
            "**必须调用 remember_memory 工具真正保存**，绝不能只在回答中口头说\"记住了\"而不调用工具。"
        )
        rn += 1
        rules.append(
            f"{rn}. 回答涉及用户背景/偏好，或想了解用户历史信息时 -> 先调用 recall_memory。"
        )
        rn += 1
    else:
        rules.append(
            f"{rn}. **长期记忆已关闭**：不要声称已保存/记住了用户信息，也不要调用记忆相关工具。"
        )
        rn += 1
    rules.append(f"{rn}. 多个都相关 -> 依次调用。")
    rn += 1
    rules.append(f"{rn}. 简单寒暄或无需工具 -> 直接回答。")
    rn += 1
    if settings.hitl_enabled and settings.hitl_actions:
        # 强制确认模式（confirm_before）：由系统在调用前强制确认，无需软性工具
        rules.append(f"{rn}. 最终必须给用户一个完整、友好的中文回答。")
    elif settings.hitl_enabled:
        # LLM 自主判定模式：像 Claude Code/Codex 一样，由模型判断何时需要请求用户授权。
        # 关键约束：用户已开启的开关=已授权，对应的能力绝不能再请求确认。
        rules.append(
            f"{rn}. **开关即授权**：用户已开启的开关（联网/知识库/记忆）表示已授权对应能力——"
            "联网搜索（web_search）、知识库检索（rag_agent）、记忆读写直接执行，"
            "**绝不要**为这些已授权的能力请求确认。"
        )
        rn += 1
        rules.append(
            f"{rn}. request_confirmation **仅**用于没有开关控制的高风险/外部操作"
            "（数据库写入、外部 MCP 调用、不可逆操作等），或用户明确要求确认时，"
            "才调用它征得用户同意；低风险只读操作（只读查询等）直接执行。"
        )
        rn += 1
        rules.append(f"{rn}. 最终必须给用户一个完整、友好的中文回答。")
    else:
        rules.append(f"{rn}. 最终必须给用户一个完整、友好的中文回答。")

    return (
        "你是一个多 Agent 平台的主管协调者，负责调度下面的专业 Agent 工具：\n\n"
        + "\n".join(tool_lines)
        + "\n\n决策规则：\n"
        + "\n".join(rules)
    )


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

    # HITL：哪些子 Agent 需要调用前人工确认（search/rag/mcp）。
    # 有前端开关控制的动作（rag/search/remember）在对应开关打开时自动豁免——
    # 开关即用户授权，不再逐次确认；HITL 仅对无开关的外部操作（如 mcp）按
    # hitl_actions 配置生效。
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
        system_prompt=_build_supervisor_prompt(use_rag, use_search, use_memory),
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
    config = {"configurable": {"thread_id": session_id}} if (cp and session_id) else None
    # Time Travel：从指定历史 checkpoint 继续（LangGraph 据此 fork 新分支）
    if config and checkpoint_id:
        config["configurable"]["checkpoint_id"] = checkpoint_id
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
            summary = ""
            for m in reversed(msgs):
                if getattr(m, "type", "") == "ai":
                    summary = extract_text(getattr(m, "content", ""))
                    break
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
            if name in ("rag_agent", "mcp_agent", "web_search"):
                used_agents.append(name)
            tool_calls_log.append(name)
    # 最终答案：逆序找最后一条 AI 消息（避免工具末轮取到原始输出）
    answer = extract_text(getattr(msgs[-1], "content", ""))
    for m in reversed(msgs):
        if getattr(m, "type", "") == "ai":
            answer = extract_text(m.content)
            break
    return list(dict.fromkeys(used_agents)), list(dict.fromkeys(tool_calls_log)), answer


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

    try:
        async with asyncio.timeout(settings.agent_timeout):
            result = await graph.ainvoke(
                input_data,
                config=config,
                context=UserContext(user_id=user_id or "default"),
            )
    except TimeoutError as exc:
        if on_event:
            await on_event({"type": "error", "content": "处理超时，请重试或简化问题"})
        raise AgentTimeoutError("Agent 执行超时") from exc
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
    # 工具调用前的 token 缓冲（supervisor 先输出"开场白"再调工具）：
    # 缓冲直到确定是否调用工具——有工具则工具执行后补推开场白+答案（连贯，不悬停）；
    # 无工具（直接回答）则流结束时补推（保持内容完整）。
    pre_tool_text: list[str] = []
    saw_tool_call = False
    flushed_prelude: str | None = None  # 工具调用前已推出的开场白（工具后答案去重用）

    async def _push(text: str) -> None:
        """推送一段文本到答案流（并记录到 answer_parts）。"""
        answer_parts.append(text)
        if on_token:
            await on_token(text)

    try:
        async with asyncio.timeout(settings.agent_timeout):
            async for mode, data in graph.astream(
                input_data,
                config=config,
                context=UserContext(user_id=user_id or "default"),
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
                            name = getattr(m, "name", "")
                            if name in ("rag_agent", "mcp_agent", "web_search"):
                                used_agents.append(name)
                            tool_calls_log.append(name)
                            saw_tool_call = True
                            # 工具调用前：先把缓冲的开场白完整输出（在工具执行前显示）
                            if pre_tool_text:
                                flushed_prelude = "".join(pre_tool_text)
                                await _push(flushed_prelude)
                                pre_tool_text.clear()
                            if on_event:
                                await on_event({"type": "tool", "content": f"工具: {name}"})
                elif mode == "messages":
                    chunk, meta = data
                    # 只流式顶层 supervisor（model 节点）的 AI 文本 token。
                    # checkpoint_ns 用 "|" 分隔嵌套任务：顶层形如 "model:<task_id>"，
                    # 子 Agent 形如 "model:<id>|mcp_agent:<id>"（含 "|"），跳过后者。
                    if "|" in (meta.get("langgraph_checkpoint_ns") or ""):
                        continue  # 子 Agent 的嵌套命名空间
                    if not isinstance(chunk, AIMessageChunk):
                        continue  # 只取 AI 生成文本，排除工具结果
                    text = extract_text(getattr(chunk, "content", ""))
                    if not text:
                        continue
                    if saw_tool_call:
                        # 工具已调用：后续为最终答案。LLM 在工具后常重新生成完整回答
                        # （重复了开场白）→ 去掉重复的前缀，接着开场白继续输出
                        if flushed_prelude is not None:
                            prelude = flushed_prelude
                            flushed_prelude = None
                            if text.startswith(prelude):
                                text = text[len(prelude):].lstrip()
                            else:
                                # 退而求其次：按开场白首句（到句号）截断重复前缀
                                first_sentence = prelude.split("。", 1)[0] + "。"
                                if (
                                    first_sentence != "。"
                                    and text.startswith(first_sentence)
                                ):
                                    text = text[len(first_sentence):].lstrip()
                        if text:
                            await _push(text)
                    else:
                        # 工具调用前：缓冲（可能是开场白，也可能是直接回答的内容）
                        pre_tool_text.append(text)
        # 流结束：无工具调用（直接回答）→ 补推缓冲内容
        if pre_tool_text:
            await _push("".join(pre_tool_text))
            pre_tool_text.clear()
    except TimeoutError as exc:
        if on_event:
            await on_event({"type": "error", "content": "处理超时，请重试或简化问题"})
        raise AgentTimeoutError("Agent 执行超时") from exc

    await _emit_final_events(
        on_event, list(dict.fromkeys(used_agents)), [], hitl_pending
    )

    return {
        "answer": "".join(answer_parts),
        "used_agents": list(dict.fromkeys(used_agents)),
        "tool_calls": list(dict.fromkeys(tool_calls_log)),
        "hitl_pending": hitl_pending,
    }
