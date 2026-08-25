"""检索级意图分类（规则版、零 LLM、零延迟）。

把用户 query 分成 4 类，供 RAG 检索按类型调整策略（见 retriever._get_relevant_documents）：
- fact    事实/实体类：精确词重要，收紧（不改写、默认 top_k）
- chat    口语/长句类：需触发式改写 + 放宽候选
- list    列举/清单类：放大 top_k、降低阈值、弱 rerank
- compare 对比/关系类：拆分多子查询分别检索再合并
"""
from __future__ import annotations

import re

from app.config import settings


class Intent:
    """意图枚举常量。"""

    FACT = "fact"
    CHAT = "chat"
    LIST = "list"
    COMPARE = "compare"


# 对比：含对比/差异/择优
_PAT_COMPARE = re.compile(r"对比|相比|与.+区别|和.+区别|vs|哪个(好|更)|优劣|区别|差异")
# 列举：要求列出/有哪些/清单
_PAT_LIST = re.compile(r"列(出|举)|有哪些|哪几个|有几个|清单|所有|全部")
# 口语/请求语气（触发改写）
_PAT_CHAT = re.compile(r"帮我|麻烦|请教|请问|嘛|呀|呢|吗$|请(你|您)?")


def classify(query: str) -> str:
    """规则分类：compare > list > chat > fact（顺序敏感，越特化越优先）。"""
    if not query:
        return Intent.FACT
    if _PAT_COMPARE.search(query):
        return Intent.COMPARE
    if _PAT_LIST.search(query):
        return Intent.LIST
    if _PAT_CHAT.search(query):
        return Intent.CHAT
    return Intent.FACT


def is_enabled() -> bool:
    return settings.intent_routing


def split_compare(query: str) -> list[str]:
    """对比类 query 按分隔词拆为多个子查询（供多路分别检索再合并）。"""
    parts = re.split(r"(?:和|与|vs|以及)\s*", query, flags=re.I)
    return [p.strip() for p in parts if p.strip()]