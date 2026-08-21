"""四指标聚合实现（对齐 RAGAS 口径，自研轻量版）。

指标定义：
- context_precision  : 检索结果按排名加权后的"相关块占比"。
      RAGAS 使用排名感知公式：Σ_{k} (rel_k × precision@k) / 总相关数，
      结果为 0~1，相关块越靠前越高。
- context_recall     : 标准答案中被检索块覆盖的关键信息点占比。
- faithfulness       : 答案中可由检索块支撑的句子占比（幻觉越低越高）。
- answer_relevancy   : 答案与问题的相关度（judge 0-5 分归一化到 0-1）。

空输入统一返回 0.0（并在报告里算该 case 丢标，由下游标注）。
"""
from __future__ import annotations

from typing import Sequence


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def safe_graded_list(raw: object, expected_len: int) -> list[int]:
    """把 judge 返回的分级字段规整为 int 列表（0/1/2，异常补 0）。

    0=不相关，1=部分相关/上下文，2=直接命中/核心。
    """
    if not isinstance(raw, list):
        return [0] * expected_len
    out: list[int] = []
    for item in raw[:expected_len]:
        if isinstance(item, bool):
            out.append(2 if item else 0)
        elif isinstance(item, (int, float)):
            out.append(max(0, min(2, int(item))))
        elif isinstance(item, str):
            s = item.strip().lower()
            if s in {"2", "high", "核心", "直接命中", "yes", "true"}:
                out.append(2)
            elif s in {"1", "medium", "相关", "部分", "partial"}:
                out.append(1)
            else:
                out.append(0)
        else:
            out.append(0)
    out.extend([0] * (expected_len - len(out)))
    return out


def dcg_at_k(relevances: Sequence[int], k: int) -> float:
    """DCG@K = Σ (2^rel_i - 1) / log2(i+1)，位置越靠前折扣越小。"""
    import math

    total = 0.0
    for i, rel in enumerate(relevances[:k], start=1):
        total += (2 ** rel - 1) / math.log2(i + 1)
    return total


def idcg_at_k(relevances: Sequence[int], k: int) -> float:
    """IDCG：按相关度降序排列（理想排序）下的 DCG@K。"""
    return dcg_at_k(sorted(relevances, reverse=True), k)


def ndcg_at_k(relevances: Sequence[int], k: int) -> float:
    """NDCG@K = DCG@K / IDCG@K（0~1）。无相关项时返回 0。"""
    idcg = idcg_at_k(relevances, k)
    if idcg <= 0:
        return 0.0
    return dcg_at_k(relevances, k) / idcg


def context_precision(ranked_relevance: Sequence[bool]) -> float:
    """位置加权相关度：'相关块靠前' 得分更高。"""
    hits = 0
    total = 0.0
    for rank, relevant in enumerate(ranked_relevance, start=1):
        if relevant:
            hits += 1
            total += hits / rank
    return _safe_ratio(total, hits)


def context_recall(covered: Sequence[bool]) -> float:
    """关键信息点覆盖率。"""
    return _safe_ratio(sum(bool(c) for c in covered), len(covered))


def faithfulness(supported: Sequence[bool]) -> float:
    """答案句子可支撑率（幻觉低 → 高）。"""
    return _safe_ratio(sum(bool(s) for s in supported), len(supported))


def answer_relevancy(score_0_5: float | int | None) -> float:
    """0-5 归一化到 0-1（非法/缺失 → 0.0）。"""
    try:
        s = float(score_0_5)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, s / 5.0))


def safe_bool_list(raw: object, expected_len: int) -> list[bool]:
    """把 judge 返回的任意字段规整为布尔列表；长度不足/异常统统补 False。"""
    if not isinstance(raw, list):
        return [False] * expected_len
    out: list[bool] = []
    for item in raw[:expected_len]:
        if isinstance(item, str):
            out.append(item.strip().lower() in {"true", "1", "yes", "是", "正确"})
        else:
            out.append(bool(item))
    out.extend([False] * (expected_len - len(out)))
    return out


def macro_average(scores: Sequence[float]) -> float:
    return _safe_ratio(sum(float(s) for s in scores), len(scores))