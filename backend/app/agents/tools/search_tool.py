"""Tavily 联网搜索工具（直接工具，不经子 Agent）。"""
from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.tools.confirmation import _confirm_or_cancel
from app.config import settings
from app.rag.prompt_injection import detect_injection, wrap_as_data

logger = logging.getLogger(__name__)


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
        dropped = 0
        for i, r in enumerate(results[: settings.tavily_max_results], 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = (r.get("content") or "").strip()
            if len(content) > 600:
                content = content[:600] + "…"
            detected, pats = detect_injection(f"{title}\n{content}")
            if detected:
                dropped += 1
                logger.warning("搜索结果疑似注入指令已剔除 url=%s patterns=%s", url, pats)
                continue
            lines.append(f"[{i}] {title}\n{url}\n{wrap_as_data(content)}")
        if not lines:
            return "搜索无结果。" if not dropped else "搜索结果经安全过滤后无可用内容。"
        return "\n\n".join(lines)
    return str(raw)


def _get_tavily_search_tool() -> Any:
    """返回 Tavily 搜索工具实例。"""
    from langchain_tavily import TavilySearch

    return TavilySearch(
        tavily_api_key=settings.tavily_api_key,
        max_results=settings.tavily_max_results,
    )


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
