"""HITL 确认工具与子 Agent 包装（agent_to_tool）。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.agents.tools.text import extract_text, last_ai_text
from app.config import settings

logger = logging.getLogger(__name__)


async def _confirm_or_cancel(question: str, data: dict) -> bool:
    """HITL：interrupt 请求用户确认，返回是否继续（confirmed 才继续）。"""
    choice = interrupt({"type": "confirmation", "question": question, "data": data})
    return choice == "confirmed"


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


class _AgentQuery(BaseModel):
    """子 Agent 工具的入参：交给子 Agent 处理的问题。"""

    query: str = Field(description="要交给子 Agent 处理的用户问题")


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
