"""通用文本提取辅助（供工具与图编排共用）。"""
from __future__ import annotations

from typing import Any


def extract_text(content: Any) -> str:
    """兼容字符串与 content blocks 列表（全仓统一的内容抽取入口）。

    规则：str 原样返回；list 中的 str 直接拼接，dict 块取 ``text`` 字段
    （不依赖 ``type`` 标记，兼容 text/text-delta 等变体），其余跳过。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def last_ai_text(messages: list) -> str:
    """逆序取最后一条 AI 消息的文本（无则空串）。"""
    for msg in reversed(messages):
        if getattr(msg, "type", "") == "ai":
            return extract_text(getattr(msg, "content", ""))
    return ""
