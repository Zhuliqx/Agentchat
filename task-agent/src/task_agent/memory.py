"""跨任务记忆：任务结论沉淀与召回（零依赖接口 + 内存实现）。"""
from __future__ import annotations

import re
from typing import Protocol


class TaskMemory(Protocol):
    """跨任务记忆接口：按目标召回历史结论、保存本次任务结论。"""

    async def recall(self, goal: str) -> list[str]: ...

    async def remember(self, goal: str, summary: str) -> None: ...


def _tokens(text: str) -> set[str]:
    """中文按二元组、英文按词切分（用于简单召回匹配）。"""
    out: set[str] = set()
    for m in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text or ""):
        if m.isascii():
            if len(m) >= 2:
                out.add(m.lower())
        else:
            for i in range(len(m) - 1):
                out.add(m[i : i + 2])
    return out


class InMemoryMemory:
    """进程内实现：按目标关键词重叠召回最近结论（供 demo/测试/单进程宿主）。"""

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []

    async def recall(self, goal: str) -> list[str]:
        tokens = _tokens(goal)
        hits = [s for g, s in self._items if tokens & _tokens(g)]
        return hits[-3:]

    async def remember(self, goal: str, summary: str) -> None:
        self._items = [(g, s) for g, s in self._items if g != goal]
        self._items.append((goal, summary))

    def items(self) -> list[tuple[str, str]]:
        return list(self._items)
