"""长期记忆工具（remember_memory / recall_memory，Supervisor 使用）。"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from langchain_core.tools import StructuredTool
from langgraph.runtime import get_runtime
from pydantic import BaseModel, Field

from app.config import settings
from app.db.memory_store import safe_asearch, store_has_index

logger = logging.getLogger(__name__)


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

            # 语义去重：仅当 Store 启用了索引时尝试（无索引直接跳过，不发已知失败调用）；
            # 检索失败 → None → 跳过语义去重，照常新增。
            similar = (
                await safe_asearch(rt.store, namespace, query=content, limit=3)
                if store_has_index()
                else []
            )
            if similar:
                top = similar[0]
                top_score = getattr(top, "score", None)
                if (
                    top_score is not None
                    and float(top_score) >= settings.memory_dedup_threshold
                ):
                    await rt.store.aput(namespace, top.key, {"content": content})
                    return "已更新长期记忆（与已有记忆高度相似，合并覆盖）。"

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

            items = await safe_asearch(rt.store, namespace, limit=50) or []
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
