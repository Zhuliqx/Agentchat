"""Agent 工具构建。

- RAG Agent：检索知识库 + 生成
- MCP Agent：调用 MCP 工具（自建 + 外部）
- web_search：直接 Tavily 联网搜索工具（非子 Agent）
- 长期记忆工具：remember_memory / recall_memory（Supervisor 使用）
- code_agent：受限沙箱执行 Python（受 CODE_AGENT_ENABLED 控制）
- request_confirmation：HITL 人工确认工具（未配置自动确认动作时注册）
- agent_to_tool：把子 Agent 包装成工具，供 Supervisor 调用（层级模式）
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from langgraph.runtime import get_runtime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.middleware import resilience_middleware
from app.config import settings
from app.mcp_integration.client import get_mcp_manager
from app.rag.retriever import get_retriever

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """你是一个严谨的知识库问答助手。

规则：
1. 仅基于检索到的文档内容回答，不要编造事实。
2. 检索结果按相关性排序，优先采信分数更高的内容；多条内容一致时综合归纳，信息冲突时如实说明差异。
3. 如果检索结果不足以回答，明确告知用户"知识库中没有相关信息"，不要猜测或编造。
4. 回答使用中文，条理清晰；引用来源时标注来源文件（如「来源：company.md」）。
5. 避免重复罗列相同信息，把相关片段整合成连贯、完整的回答。
6. 必须调用 search_knowledge_base 工具获取上下文，再作答。
7. 【归纳推理】当问题明确需要对比、筛选或归纳（如比较两个对象、判断哪个套餐满足条件）时，
   可综合多个检索块中分散的信息推理作答；但若检索内容完全无法支撑答案，必须如实说
   "知识库中没有相关信息"，**禁止为凑出答案而把不同来源的信息强行拼凑**（如把 A 产品
   规则套用到 B 产品）。普通事实型问题直接给出准确信息即可，不要额外扩展。"""

MCP_SYSTEM_PROMPT = """你是一个工具调用专家，负责使用 MCP 工具完成用户请求。

规则：
1. 根据用户问题选择合适的工具（数据库查询、时间、外部工具等）。
2. 数据库只允许执行只读 SELECT/WITH 查询。
3. 把工具返回结果整理成清晰、易读的答案。
4. 如果所有工具都无法完成任务，如实说明原因。"""


class _SearchQuery(BaseModel):
    """web_search（直接 Tavily 搜索）工具入参。"""

    query: str = Field(description="搜索关键词（1-3 个精准关键词，中文或英文均可）")


def _format_search_results(raw: Any) -> str:
    """把 Tavily 原始结果格式化为紧凑文本（供 Supervisor 总结）。"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        results = raw.get("results") or []
        lines = []
        for i, r in enumerate(results[: settings.tavily_max_results], 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = (r.get("content") or "").strip()
            if len(content) > 600:
                content = content[:600] + "…"
            lines.append(f"[{i}] {title}\n{url}\n{content}")
        return "\n\n".join(lines) or "搜索无结果。"
    return str(raw)


def _get_tavily_search_tool() -> Any:
    """返回 Tavily 搜索工具实例。"""
    from langchain_tavily import TavilySearch

    return TavilySearch(
        tavily_api_key=settings.tavily_api_key,
        max_results=settings.tavily_max_results,
    )


async def _confirm_or_cancel(question: str, data: dict) -> bool:
    """HITL：interrupt 请求用户确认，返回是否继续（confirmed 才继续）。"""
    choice = interrupt({"type": "confirmation", "question": question, "data": data})
    return choice == "confirmed"


def _make_search_arun(tool: Any, confirm_before: bool):
    """构造直接搜索的 arun：HITL 确认 → 单次 Tavily 调用 → 格式化结果。"""

    async def _invoke(query: str) -> Any:
        try:
            return await tool.ainvoke(query)
        except TypeError:  # 兼容旧版 TavilySearchResults(dict 入参)
            return await tool.ainvoke({"query": query})

    async def _arun(query: str) -> str:
        if confirm_before:
            if not await _confirm_or_cancel(
                f"确认进行联网搜索：{query[:80]}{'…' if len(query) > 80 else ''}？",
                {"action": "web_search", "query": query},
            ):
                return "操作已取消：用户未确认联网搜索。"
        try:
            return _format_search_results(await _invoke(query))
        except Exception as exc:
            return f"联网搜索失败: {exc}"

    return _arun


def build_search_tool(confirm_before: bool = False) -> Optional[StructuredTool]:
    """直接 Tavily 联网搜索工具（不经子 Agent）。

    相比旧的子 Agent 方案（ReAct 多轮：LLM 生成关键词 → 搜索 → LLM 总结，
    实测一次搜索会触发 4 次 Tavily + 3 次 LLM，耗时 ~23s），这里把 Tavily
    作为直接工具交给 Supervisor：单次调用 + Supervisor 直接总结，搜索环节
    从 ~23s 降到 ~3s。confirm_before=True 时先 interrupt 人工确认。
    """
    if not settings.tavily_api_key:
        return None
    try:
        tool = _get_tavily_search_tool()
    except ImportError:
        # 兜底：旧版 langchain-community 实现
        try:
            import os

            from langchain_community.tools.tavily_search import TavilySearchResults

            os.environ.setdefault("TAVILY_API_KEY", settings.tavily_api_key)
            tool = TavilySearchResults(
                api_key=settings.tavily_api_key,
                max_results=settings.tavily_max_results,
            )
        except ImportError:
            logger.warning("未安装 langchain-tavily / langchain-community，联网搜索不可用")
            return None
    return StructuredTool(
        name="web_search",
        description=(
            "联网搜索：直接调用 Tavily 检索最新网络资讯（新闻、行情、实时信息）。"
            "**传入 1-3 个精准搜索关键词**（而非完整问题），如「2026 AI 行业 新闻」。"
        ),
        args_schema=_SearchQuery,
        coroutine=_make_search_arun(tool, confirm_before),
    )

CODE_SYSTEM_PROMPT = """你是一个 Python 代码专家，负责编写并执行代码解决用户的计算/算法/逻辑问题。

规则：
1. 需要实际计算、验证逻辑、运行算法或生成数据时，**必须**调用 execute_python_code 执行代码，并基于真实运行结果回答。
2. 生成的代码要简洁、正确；执行报错时根据错误信息修正后重试（最多重试 2 次）。
3. 执行环境为受限沙箱：仅支持纯计算标准库（math/json/datetime/random/collections/itertools/re 等），
   禁止文件读写、网络、子进程；代码会超时（默认 15s）并截断输出。
4. 用中文回答：先说明思路，再给出代码与运行结果，最后总结结论。
5. 纯粹的知识问答/解释代码不一定要执行；只有涉及实际计算或验证时才执行。"""


@lru_cache(maxsize=1)
def build_rag_agent():
    """构建 RAG 子 Agent（检索工具 + LLM）。缓存，避免不同开关组合重复构建。"""
    from langchain.agents import create_agent

    return create_agent(
        get_llm("light"),  # 子 Agent 用轻量模型（配置了 LLM_LIGHT_MODEL 时）
        tools=[_build_search_knowledge_base_tool()],
        system_prompt=RAG_SYSTEM_PROMPT,
        middleware=[resilience_middleware()],
    )


class _RagQuery(BaseModel):
    """search_knowledge_base 工具入参。"""

    query: str = Field(description="要检索知识库的用户问题")


# ---- 引用溯源：最近一次知识库检索命中的来源（按 user_id 单槽） ----
# 检索工具执行后写入，Supervisor 发 tool 事件时读取附加到 data.sources。
_RAG_SOURCES: dict[str, list[str]] = {}
_RAG_SOURCES_LOCK = threading.Lock()


def _record_rag_sources(user_id: str, sources: list[str]) -> None:
    with _RAG_SOURCES_LOCK:
        _RAG_SOURCES[user_id] = list(sources)


def get_recent_rag_sources(user_id: str) -> list[str]:
    with _RAG_SOURCES_LOCK:
        return list(_RAG_SOURCES.get(user_id, []))


def _build_search_knowledge_base_tool() -> StructuredTool:
    """知识库检索工具（按用户隔离）。

    从 LangGraph 运行时上下文读取当前 user_id，只检索该用户的知识库，
    避免跨用户泄露文档内容（与 remember/recall 记忆工具同一机制）。
    检索命中的来源会记录到 _RAG_SOURCES（按 user_id），供前端「引用溯源」展示。
    """

    async def _arun(query: str) -> str:
        try:
            rt = get_runtime()
            user = getattr(rt.context, "user_id", "default") or "default"
            retriever = get_retriever(user_id=user)
            docs = await asyncio.to_thread(retriever.invoke, query)
            if not docs:
                return "知识库中没有检索到相关内容。"
            sources: list[str] = []
            parts = []
            for i, d in enumerate(docs, 1):
                src = d.metadata.get("source", "")
                name = Path(src).name if src else ""
                if src and src not in sources:
                    sources.append(src)
                parts.append(f"[{i}] 来源: {name}\n{d.page_content}")
            # 记录最近检索来源（供引用溯源）
            _record_rag_sources(user, sources)
            return "\n\n".join(parts)
        except Exception as exc:
            return f"知识库检索失败: {exc}"

    return StructuredTool(
        name="search_knowledge_base",
        description="在向量知识库中按语义检索相关内容。当问题涉及知识库文档时使用。",
        args_schema=_RagQuery,
        coroutine=_arun,
    )


@lru_cache(maxsize=1)
def build_mcp_agent():
    """构建 MCP 子 Agent（当前已连接的所有 MCP 工具 + LLM）。缓存。"""
    from langchain.agents import create_agent

    mcp_tools = get_mcp_manager().get_langchain_tools()
    return create_agent(
        get_llm("light"),  # 子 Agent 用轻量模型
        tools=mcp_tools,
        system_prompt=MCP_SYSTEM_PROMPT,
        middleware=[resilience_middleware()],
    )


# ---------------- 代码 Agent（受限沙箱执行 Python） ----------------

class _CodeExecQuery(BaseModel):
    """execute_python_code 工具入参。"""

    code: str = Field(description="要执行的 Python 代码（受限沙箱，仅纯计算标准库）")


@lru_cache(maxsize=1)
def build_code_agent():
    """构建代码 Agent（受限执行 Python + LLM）。缓存。"""
    from langchain.agents import create_agent

    return create_agent(
        get_llm("light"),
        tools=[_build_execute_python_tool()],
        system_prompt=CODE_SYSTEM_PROMPT,
        middleware=[resilience_middleware()],
    )


def _build_execute_python_tool() -> StructuredTool:
    """受限执行 Python 代码，返回 stdout/错误。"""

    def _run(code: str) -> str:
        from app.agents.code_executor import execute_code

        r = execute_code(
            code,
            timeout=settings.code_exec_timeout,
            max_output=settings.code_exec_max_output,
        )
        lines = []
        if r["stdout"]:
            lines.append("stdout:\n" + r["stdout"])
        if r["stderr"]:
            lines.append("stderr:\n" + r["stderr"])
        if r["error"]:
            lines.append("错误:\n" + r["error"])
        if not lines:
            lines.append("(代码执行完成，无输出)")
        return "\n\n".join(lines)

    return StructuredTool(
        name="execute_python_code",
        description=(
            "在受限沙箱中执行一段 Python 代码并返回运行结果（stdout/错误）。"
            "当需要实际计算、验证算法、生成数据或运行脚本时使用；"
            "环境仅支持纯计算标准库（math/json/datetime/random/collections 等），禁文件/网络/子进程。"
        ),
        args_schema=_CodeExecQuery,
        func=_run,
    )


# ---------------- 长期记忆工具（Supervisor 使用） ----------------

class _MemoryContent(BaseModel):
    """remember_memory 工具入参。"""

    content: str = Field(description="要长期记住的用户信息/偏好/事实（一句话）")


class _NoArgs(BaseModel):
    """无参工具占位模型。"""

    unused: Optional[str] = Field(default=None, description="")


def build_remember_tool() -> StructuredTool:
    """保存一条长期记忆（跨会话有效，写入 LangGraph Store）。

    写入前做语义去重：若与已有记忆高度相似（余弦 >= 阈值），更新该条而非新增。
    """

    async def _arun(content: str) -> str:
        try:
            rt = get_runtime()
            user = getattr(rt.context, "user_id", "default")
            if rt.store is None:
                return "长期记忆存储不可用（Store 未初始化）。"
            namespace = (user, "memories")

            # 语义去重：仅当 Store 启用了索引且能返回相似度时生效
            try:
                similar = await rt.store.asearch(namespace, query=content, limit=3)
                top = similar[0] if similar else None
                top_score = getattr(top, "score", None) if top is not None else None
                if (
                    top is not None
                    and top_score is not None
                    and float(top_score) >= settings.memory_dedup_threshold
                ):
                    await rt.store.aput(namespace, top.key, {"content": content})
                    return "已更新长期记忆（与已有记忆高度相似，合并覆盖）。"
            except Exception:  # 无索引 / 不支持 query → 跳过去重
                pass

            key = uuid.uuid4().hex
            await rt.store.aput(namespace, key, {"content": content})
            return "已保存到长期记忆，下次对话仍会记得。"
        except Exception as exc:
            return f"保存记忆失败: {exc}"

    return StructuredTool(
        name="remember_memory",
        description=(
            "保存一条重要的用户信息/偏好/事实到长期记忆（跨会话有效）。"
            "当用户透露值得长期记住的内容（如个人偏好、公司信息、约定、身份）时调用。"
        ),
        args_schema=_MemoryContent,
        coroutine=_arun,
    )


def build_recall_tool() -> StructuredTool:
    """读取当前用户的长期记忆（从 LangGraph Store 全量列出，最多 50 条）。"""

    async def _arun(unused: Optional[str] = None) -> str:
        try:
            rt = get_runtime()
            user = getattr(rt.context, "user_id", "default")
            if rt.store is None:
                return "长期记忆存储不可用（Store 未初始化）。"
            namespace = (user, "memories")

            items = []
            try:
                items = await rt.store.asearch(namespace, limit=50)
            except Exception:  # pragma: no cover
                items = []
            if not items:
                return "当前没有保存的长期记忆。"
            lines = [f"- {i.value.get('content', '')}" for i in items]
            return "该用户的长期记忆（跨会话）：\n" + "\n".join(lines)
        except Exception as exc:
            return f"读取记忆失败: {exc}"

    return StructuredTool(
        name="recall_memory",
        description=(
            "读取当前用户的长期记忆。当回答涉及用户背景、偏好或历史信息时，先调用此工具。"
        ),
        args_schema=_NoArgs,
        coroutine=_arun,
    )


class _AgentQuery(BaseModel):
    """子 Agent 工具的入参：交给子 Agent 处理的问题。"""

    query: str = Field(description="要交给子 Agent 处理的用户问题")


class _ConfirmQuery(BaseModel):
    """request_confirmation 工具入参。"""

    question: str = Field(description="需要用户人工确认的问题")
    data: str = Field(default="", description="附加信息（可选）")


def build_confirmation_tool() -> StructuredTool:
    """人工确认工具：supervisor 在需要确认时调用，暂停图等待用户选择。

    依赖 LangGraph interrupt：工具内调用会暂停图执行并返回 __interrupt__，
    恢复时用 Command(resume=<choice>) 继续，choice 即用户的选择。
    """

    async def _arun(question: str, data: str = "") -> str:
        choice = interrupt(
            {"type": "confirmation", "question": question, "data": data}
        )
        return f"用户确认结果: {choice}"

    return StructuredTool(
        name="request_confirmation",
        description=(
            "请求用户确认/授权后再继续。**仅**当操作没有对应开关且风险较高（数据库写入、外部 MCP、"
            "不可逆操作等），或用户明确要求确认时调用；用户已开启开关的能力（联网/知识库/记忆）"
            "已获授权，不要调用本工具。用户返回 confirm 后继续，返回 cancelled 则放弃该操作。"
        ),
        args_schema=_ConfirmQuery,
        coroutine=_arun,
    )


def agent_to_tool(
    agent: Any,
    name: str,
    description: str,
    confirm_before: bool = False,
) -> StructuredTool:
    """把子 Agent 包装成工具，供 Supervisor 调用。

    confirm_before=True 时，实际调用子 Agent 前先 interrupt 请求人工确认；
    用户未确认则直接返回"已取消"，不触发子 Agent（用于 HITL 场景，如联网搜索）。
    """

    async def _arun(query: str) -> str:
        if confirm_before:
            if not await _confirm_or_cancel(
                f"确认调用 {name} 处理：{query[:80]}{'…' if len(query) > 80 else ''}？",
                {"action": name, "query": query},
            ):
                return f"操作已取消：用户未确认调用 {name}。"
        # 子 Agent 调用容错：失败按退避重试（subagent_retries 次）
        retries = settings.subagent_retries
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = await agent.ainvoke({"messages": [("user", query)]})
                messages = result["messages"]
                return last_ai_text(messages) or extract_text(
                    getattr(messages[-1], "content", str(messages[-1]))
                )
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.5 * (2**attempt))
        return f"子 Agent {name} 调用失败（已重试 {retries} 次）: {last_exc}"

    return StructuredTool(
        name=name,
        description=description,
        args_schema=_AgentQuery,
        coroutine=_arun,
    )


def extract_text(content: Any) -> str:
    """兼容字符串与 content blocks 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def last_ai_text(messages: list) -> str:
    """逆序取最后一条 AI 消息的文本（无则空串）。"""
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai":
            return extract_text(getattr(msg, "content", ""))
    return ""
