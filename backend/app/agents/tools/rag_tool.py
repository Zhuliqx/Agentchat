"""知识库检索工具（RAG Agent：检索 + 生成）。"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path

from langchain_core.tools import StructuredTool
from langgraph.runtime import get_runtime
from pydantic import BaseModel, Field

from app.agents.llm import get_llm
from app.agents.middleware import resilience_middleware
from app.agents.prompts import RAG_SYSTEM_PROMPT
from app.agents.tools.sources import _record_rag_sources
from app.config import settings
from app.rag.prompt_injection import detect_injection, wrap_as_data
from app.rag.retriever import get_retriever

logger = logging.getLogger(__name__)


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


def _build_retrieval_context(
    messages: list, max_rounds: int = 2, max_chars: int = 200
) -> str:
    """把最近几轮会话压缩为检索上文（纯函数，便于单测）。

    输入为 ``postgres.get_recent_messages`` 返回的消息对象（有 role/content 属性）。
    逆序收集最近 max_rounds 轮（一轮 = 用户提问 + 助手回答），每条截断
    max_chars 字符；无可用的历史消息返回空串。调用方把返回值拼进检索
    query（RAG_MULTI_TURN_CONTEXT 开启时），用于多轮追问/指代消解。
    """
    if not messages:
        return ""
    rounds: list[tuple[str, str]] = []
    i = len(messages) - 1
    while i >= 0 and len(rounds) < max_rounds:
        role = str(getattr(messages[i], "role", "") or "")
        if role == "assistant":
            ans = str(getattr(messages[i], "content", "") or "")[:max_chars]
            q = ""
            j = i - 1
            while j >= 0:
                r2 = str(getattr(messages[j], "role", "") or "")
                if r2 == "user":
                    q = str(getattr(messages[j], "content", "") or "")[:max_chars]
                    break
                if r2 == "assistant":
                    break  # 前一条也是助手消息：不再向前找（避免跨轮错配）
                j -= 1
            if q:
                rounds.append((q, ans))
            i = j
        else:
            i -= 1
    if not rounds:
        return ""
    lines = []
    for q, ans in reversed(rounds):
        lines.append(f"用户: {q}")
        if ans:
            lines.append(f"助手: {ans}")
    return "[上文]\n" + "\n".join(lines)


def _front_load(docs: list) -> list:
    """相关块前置（RAG_FRONT_LOAD_BEST）：最高分块放开头、次高分块放末尾。

    LLM 对上下文首尾注意力最强（lost-in-the-middle 缓解）。len < 3 时
    原样返回；仅调整顺序，不改变内容与编号（调用方按新顺序重新编号）。
    """
    if len(docs) < 3:
        return docs
    return [docs[0]] + docs[2:] + [docs[1]]


def _build_search_knowledge_base_tool() -> StructuredTool:
    """知识库检索工具（按用户隔离）。

    从 LangGraph 运行时上下文读取当前 user_id，只检索该用户的知识库，
    避免跨用户泄露文档内容（与 remember/recall 记忆工具同一机制）。
    检索命中的来源会记录到 sources（按 user_id），供前端「引用溯源」展示。
    """

    async def _arun(query: str) -> str:
        try:
            rt = get_runtime()
            user = getattr(rt.context, "user_id", "default") or "default"
            # 多轮上下文（默认关）：把最近几轮会话历史拼进检索 query，
            # 改善"那第二个呢/它的价格呢"这类指代式追问的召回。
            session_id = getattr(rt.context, "session_id", "") or ""
            if settings.rag_multi_turn_context and session_id:
                from app.db import postgres  # 延迟导入防环

                msgs = await asyncio.to_thread(postgres.get_recent_messages, session_id, 6)
                ctx = _build_retrieval_context(msgs)
                if ctx:
                    query = f"{ctx}\n当前问题: {query}"
            retriever = get_retriever(user_id=user)
            docs = await asyncio.to_thread(retriever.invoke, query)
            # 相关块前置（默认关）：最高分块放开头、次高分放末尾
            if settings.rag_front_load_best:
                docs = _front_load(docs)
            if not docs:
                return "知识库中没有检索到相关内容。"
            sources: list[str] = []
            parts = []
            dropped = 0
            for i, d in enumerate(docs, 1):
                src = d.metadata.get("source", "")
                detected, pats = detect_injection(d.page_content)
                if detected:
                    dropped += 1
                    logger.warning(
                        "检索块疑似注入指令已剔除 user=%s source=%s patterns=%s",
                        user, src, pats,
                    )
                    continue
                name = Path(src).name if src else ""
                if src and src not in sources:
                    sources.append(src)
                parts.append(f"[{i}] 来源: {name}\n{wrap_as_data(d.page_content)}")
            # 记录最近检索来源（供引用溯源）
            _record_rag_sources(user, sources)
            if not parts:
                if dropped:
                    return "知识库检索结果经安全过滤后无可用内容。"
                return "知识库中没有检索到相关内容。"
            return "\n\n".join(parts)
        except Exception as exc:
            return f"知识库检索失败: {exc}"

    return StructuredTool(
        name="search_knowledge_base",
        description="在向量知识库中按语义检索相关内容。当问题涉及知识库文档时使用。",
        args_schema=_RagQuery,
        coroutine=_arun,
    )
