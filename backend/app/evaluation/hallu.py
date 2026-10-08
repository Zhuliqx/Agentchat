"""CRUD-RAG 幻觉纠正任务（hallu_modified）的评估基础设施。

任务口径：给「新闻开头 + 幻觉续写 + 检索材料」，把续写里的幻觉改成与事实
一致的文本；参考答案为数据集人工修订版（hallucinatedMod）。

数据来源（Apache-2.0）：https://github.com/IAAR-Shanghai/CRUD_RAG
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.evaluation.dataset import DatasetError

_PROBE_LEN = 60


def normalize_text(text: str) -> str:
    """空白折叠：数据集正文含换行/多空格混排，比对前统一成单空格。"""
    return re.sub(r"\s+", " ", text or "").strip()


def make_probe(remainder: str, length: int = _PROBE_LEN) -> str:
    """正确续写的头部片段：检索命中判定用（返回空串表示无参考）。"""
    return normalize_text(remainder)[:length]


def extract_response(text: str) -> str:
    """提取 <response>...</response> 内容；无标签时返回原文（兼容不守格式的输出）。"""
    m = re.search(r"<response>(.*?)</response>", text or "", re.S)
    return (m.group(1) if m else (text or "")).strip()


def bad_keyword_hits(answer: str, bad_keywords: list[str]) -> list[str]:
    """返回答案中命中的"不合理"关键词（数据集自带标注，无需 LLM）。"""
    ans = answer or ""
    return [k for k in bad_keywords if k and k in ans]


@dataclass
class HalluCase:
    id: str
    news_beginning: str
    hallucinated_continuation: str
    reference_corrected: str
    bad_keywords: list[str] = field(default_factory=list)
    remainder_probe: str = ""
    notes: str = ""


def load_hallu_cases(path: str | Path) -> list[HalluCase]:
    """读取并校验幻觉纠正 GT 文件（crud_hallu_gt*.json）。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatasetError(f"hallu GT 不存在: {p}") from None
    except json.JSONDecodeError as exc:
        raise DatasetError(f"hallu GT JSON 解析失败: {exc}") from None

    raw_cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError(f"hallu GT 缺少非空 cases 列表: {p}")

    cases: list[HalluCase] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_cases, start=1):
        if not isinstance(raw, dict):
            raise DatasetError(f"cases[{i}] 不是对象")
        cid = str(raw.get("id") or f"case_{i:04d}").strip()
        begin = str(raw.get("news_beginning") or "").strip()
        hallu = str(raw.get("hallucinated_continuation") or "").strip()
        ref = str(raw.get("reference_corrected") or "").strip()
        if not begin or not hallu or not ref:
            raise DatasetError(
                f"case {cid} 缺少 news_beginning/hallucinated_continuation/reference_corrected"
            )
        if cid in seen:
            raise DatasetError(f"case id 重复: {cid}")
        seen.add(cid)
        kws = [str(k).strip() for k in (raw.get("bad_keywords") or []) if str(k).strip()]
        cases.append(
            HalluCase(
                id=cid,
                news_beginning=begin,
                hallucinated_continuation=hallu,
                reference_corrected=ref,
                bad_keywords=kws,
                remainder_probe=str(raw.get("remainder_probe") or "").strip(),
                notes=str(raw.get("notes") or ""),
            )
        )
    return cases
