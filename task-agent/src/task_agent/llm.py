"""LLM 最小协议与文本抽取工具（不绑定具体厂商 SDK）。"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class LLM(Protocol):
    """宿主/默认实现只需提供 async ainvoke(prompt) -> 带 .content 的响应。"""

    async def ainvoke(self, prompt: str) -> Any: ...


LLMFactory = Callable[[], LLM]


async def llm_text(llm: LLM, prompt: str) -> str:
    """单次 LLM 调用并抽取文本；失败直接抛异常（由节点级 retry/error_handler 处理）。"""
    resp = await llm.ainvoke(prompt)
    return resp.content if isinstance(resp.content, str) else str(resp.content)
