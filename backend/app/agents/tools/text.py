"""通用文本提取辅助（供工具与图编排共用）。"""
from __future__ import annotations

from typing import Any


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
