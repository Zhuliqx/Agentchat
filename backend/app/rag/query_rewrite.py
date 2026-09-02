"""检索查询改写（Query Rewriting）。

改善「口语查询 × 书面文档」的语义鸿沟。档位（``settings.query_rewrite_mode``）：

- ``none``：原样返回（默认，行为不变）；
- ``rule``：规则改写——去口语框架词 / 句尾疑问词、泛化词「并列扩展」
  （追加同义/上位词而非替换，保住原始 token，信息不丢）。零 LLM 依赖、
  确定性，CI 可挂；
- ``llm``：LLM 改写为适合检索的关键词查询，失败/异常自动回退原句。

防退化（两个关键保护）：
- ``_has_precise_token``：query 含数字/型号/英文专名时跳过 llm 档
  （LLM 改写容易把精确标识改丢，如版本号/号码）；
- 双路兜底：retriever 侧把「原 query + 改写结果」都检索（见
  ``retriever._expand_queries``），改写丢信息时原句兜住。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

_LLM_REWRITE_PROMPT = """你是知识库检索查询改写器。将用户的问题改写为适合在知识库中检索的关键词查询，用于改善检索效果。
规则：
1. 提取核心检索词（实体/名词短语/数字/型号），去掉口语语气词与冗余表达；
2. 中文口语 → 书面语，必要时补充同义词；
3. 必须保留原文中的数字/版本号/型号/英文专名，不要改写或翻译；
4. 只输出一行改写结果，不要解释、不要客套、不要引号。

示例：
问题: 公司是啥时候成立的呀
改写: 公司成立年份
问题: 帮我看看那个旗舰产品
改写: 旗舰产品
问题: 试用版一个月多少钱
改写: 试用版 价格 费用"""

# LLM 未真正执行改写时的常见模板回复 → 视为改写失败，回退原 query
_REFUSAL_MARKS = ("请提供", "您好", "欢迎", "请问您", "请告诉我", "请描述", "请输入", "好的")

# 句首口语框架词（删除；写成"前缀组合"，一次尽量去干净，如「请帮我」→ 空）
_STRIP_PREFIX = re.compile(
    r"^(请|麻烦你?|请问|给我)?\s*(帮我?|我想(知道|问(一下)?|了解|查(一下)?)|"
    r"我要(查|找|问|知道|了解))?\s*"
)
_STRIP_SUFFIX = re.compile(r"(谢谢|感谢).*$")
_TAIL_PARTICLE = re.compile(r"[吗呢啊]$")

# 泛化词 → 并列追加的同义/上位词（不改原文，仅扩展供 BM25/向量双通道命中）
# 顺序敏感："多少钱" 在 "价格" 前，避免先命中短键后长键重复追加
_GENERALIZE: dict[str, str] = {
    "多少钱": " 价格 费用 定价",
    "哪个套餐": " 版本",
    "什么产品": " 产品 平台",
    "有哪些": " 包含 功能",
    "有什么": " 包含 功能",
    "多少成员": " 人数",
    "价格": " 费用 定价",
    "套餐": " 版本",
    "多少": " 数量",
}

# 精确标识：数字或 ≥2 字母的英文串（版本号/型号/号码/专名）
_PRECISE_TOKEN = re.compile(r"[0-9]|[A-Za-z]{2,}")


def _has_precise_token(query: str) -> bool:
    """是否含精确标识——是则跳过 llm 改写（防精确信息被改写丢）。"""
    return bool(_PRECISE_TOKEN.search(query))


def _rule_rewrite(query: str) -> str:
    """规则改写：删框架词/句尾疑问词 + 泛化词并列扩展（短 query 不扩展）。"""
    q = _STRIP_SUFFIX.sub("", query)
    q = _STRIP_PREFIX.sub("", q).strip()
    q = re.sub(r"[？?！!。、]+$", "", q).strip()
    q = _TAIL_PARTICLE.sub("", q).strip() or q
    if len(q) >= 4:
        for key, extra in _GENERALIZE.items():
            if key in q and extra.strip() not in q:
                q += extra
    return q


def _clean_llm_output(text: str, query: str) -> str:
    """清洗 LLM 输出；拒绝模板回复/超长/空 → 回退原 query。"""
    if any(m in text for m in _REFUSAL_MARKS):
        return query
    if "改写" in text:
        text = text.split("改写", 1)[-1].lstrip(":： ")
    text = text.strip(" \n\"'「」『』")
    if not text or len(text) < 2 or len(text) > 60:
        return query
    return text


def _llm_rewrite(query: str) -> str:
    """LLM 改写为检索 query；异常/拒绝模板/空/超长一律回退原句。"""
    from app.agents.llm import get_llm  # 延迟导入，避免循环依赖
    from app.agents.tools.text import extract_text  # 延迟导入，避免包级循环
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        resp = get_llm("light").invoke(
            [SystemMessage(content=_LLM_REWRITE_PROMPT), HumanMessage(content=query)]
        )
        text = extract_text(getattr(resp, "content", "")).strip()
    except Exception as exc:
        logger.warning("LLM 改写失败，回退原 query: %s", exc)
        return query
    return _clean_llm_output(text, query)


@lru_cache(maxsize=settings.query_rewrite_cache_size)
def _rewrite_cached(mode: str, query: str) -> str:
    """带缓存的核心改写（进程内；相同 (mode, query) 只算一次，评估 A/B 不重复计费）。"""
    if mode == "rule":
        return _rule_rewrite(query)
    if mode == "llm":
        if _has_precise_token(query):
            return query
        return _llm_rewrite(query)
    return query


def rewrite_query(query: str, mode: str | None = None) -> str:
    """查询改写主入口。返回改写后的单条 query；关闭或 mode=none 时原样返回。"""
    mode = (mode or settings.query_rewrite_mode).lower()
    if not settings.query_rewrite_enabled or mode in ("", "none"):
        return query
    q = query.strip()
    if not q:
        return query
    return _rewrite_cached(mode, q)
