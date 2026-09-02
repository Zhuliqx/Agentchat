"""对话历史自动压缩中间件（SummarizationMiddleware 安全模式封装）。

复用 LangChain 1.3 自带的 ``SummarizationMiddleware``（触发判断 / token 统计 /
安全切点（AI/Tool 消息成对保留）/ ``RemoveMessage`` 状态替换），仅做两处改造：

- 摘要提示词改为中文对话场景（主题 / 关键事实 / 资料来源 / 未决事项）；
- **失败不裁剪**：摘要模型调用失败或返回空时本轮跳过压缩、保留完整历史，
  避免框架默认行为（失败仍裁剪并用错误文本占位）造成上下文丢失。

用法：``build_history_summary_middleware(model=get_llm("light"))``，关闭时返回
``None``（调用方过滤后不挂载）。
"""
from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage
from langchain_core.messages.utils import get_buffer_string
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.config import settings

logger = logging.getLogger(__name__)

HISTORY_SUMMARY_PROMPT = """你是对话摘要助手。把下面的对话历史压缩成一份中文摘要，
供后续回答继续使用。要求：
1. 覆盖：会话主题/用户目标、已确认的关键事实与结论、已查到的资料/信息来源、尚未解决的问题；
2. 保留数字、专名、结论，不编造摘要中没有的信息；
3. 只输出摘要正文，不要解释，不要额外开场白。

对话历史：
{messages}
"""


class SafeSummarizationMiddleware(SummarizationMiddleware):
    """安全模式对话摘要中间件：摘要失败/为空时不裁剪历史。"""

    # ---------- 摘要生成：失败/为空 → None（不返回错误文本） ----------
    def _create_summary(self, messages_to_summarize: list[Any]) -> str | None:
        if not messages_to_summarize:
            return None
        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return None
        formatted_messages = get_buffer_string(trimmed_messages, format="xml")
        try:
            response = self.model.invoke(
                self.summary_prompt.format(messages=formatted_messages).rstrip(),
                config={"metadata": {"lc_source": "summarization"}},
            )
            text = (getattr(response, "text", None) or "").strip()
            return text or None
        except Exception:  # noqa: BLE001 - 摘要失败由调用方决定跳过压缩
            logger.warning("对话摘要生成失败，本轮跳过压缩（同步调用）", exc_info=True)
            return None

    async def _acreate_summary(self, messages_to_summarize: list[Any]) -> str | None:
        if not messages_to_summarize:
            return None
        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return None
        formatted_messages = get_buffer_string(trimmed_messages, format="xml")
        try:
            response = await self.model.ainvoke(
                self.summary_prompt.format(messages=formatted_messages).rstrip(),
                config={"metadata": {"lc_source": "summarization"}},
            )
            text = (getattr(response, "text", None) or "").strip()
            return text or None
        except Exception:  # noqa: BLE001
            logger.warning("对话摘要生成失败，本轮跳过压缩", exc_info=True)
            return None

    # ---------- 摘要消息：中文标记，lc_source 供测试/观测识别 ----------
    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        return [
            HumanMessage(
                content=f"以下是本次对话的摘要：\n\n{summary}",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]

    # ---------- 主流程：摘要为空时跳过裁剪，其余照搬基类 ----------
    def before_model(
        self, state: dict[str, Any], runtime: Any
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)
        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None
        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None
        messages_to_summarize, preserved_messages = self._partition_messages(
            messages, cutoff_index
        )
        summary = self._create_summary(messages_to_summarize)
        if not summary:
            return None  # 摘要失败/为空 → 保留完整历史，本轮不压缩
        new_messages = self._build_new_messages(summary)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    async def abefore_model(
        self, state: dict[str, Any], runtime: Any
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)
        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None
        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None
        messages_to_summarize, preserved_messages = self._partition_messages(
            messages, cutoff_index
        )
        summary = await self._acreate_summary(messages_to_summarize)
        if not summary:
            return None  # 摘要失败/为空 → 保留完整历史，本轮不压缩
        new_messages = self._build_new_messages(summary)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }


def build_history_summary_middleware(model: Any | None = None) -> Any | None:
    """按配置构建摘要中间件；``history_summary_enabled=False`` 时返回 None。"""
    if not settings.history_summary_enabled:
        return None
    if model is None:
        from app.agents.llm import get_llm

        model = get_llm("light")
    return SafeSummarizationMiddleware(
        model=model,
        trigger={
            "tokens": settings.history_summary_trigger_tokens,
            "messages": settings.history_summary_min_messages,
        },
        keep=("messages", settings.history_summary_keep_messages),
        summary_prompt=HISTORY_SUMMARY_PROMPT,
        trim_tokens_to_summarize=settings.history_summary_max_input_tokens,
    )
