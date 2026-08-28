"""检索结果后处理（纯函数管线，与 Milvus/检索器解耦）。

由 ``retriever._finalize`` 按顺序调用：
去重合并（同文档限流 + 相邻块合并）→ 指纹/语义近似去重 → 总字符预算 → 块截断。
独立成模块便于单测与按开关组合扩展（如 RAG_FRONT_LOAD_BEST 类的新后处理）。
"""
from __future__ import annotations

import re

from app.config import settings


def _hit_key(h: dict) -> float:
    """命中的排序分数（rerank > rrf > 向量相似度 > bm25，取首个可用）。"""
    for k in ("rerank_score", "rrf_score", "score", "bm25_score"):
        v = h.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _normalize_text(text: str) -> str:
    """文本归一化：去空白/标点/低权重符号，用于指纹去重判重。"""
    return re.sub(r"\s+|[，。、；：？！,.?!:;\"'、\-_]", "", text or "").lower()


def _cosine(a: list, b: list) -> float:
    """向量余弦相似度（用于语义近似去重）。"""
    if not a or not b:
        return 0.0
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _best_score(hits: list[dict]) -> float:
    """候选集最高分（rerank > rrf > score），作为置信信号。"""
    if not hits:
        return 0.0
    best = 0.0
    for h in hits:
        s = _hit_key(h)
        if s is not None:
            best = max(best, s)
    return float(best)


def _merge_multi(hits: list[dict]) -> list[dict]:
    """多路（原 query + 改写）检索结果合并：按 (source, chunk_index) 保最高分。

    注意：多路的 rrf/向量分数各自独立量纲，不能直接跨路比较，这里"保最高分"
    只是近似去重；真正统一量纲依赖后续 rerank（开启时）。chunk_index 缺失的
    结果用 vector_id 兜底区分，避免不同块被误并。
    """
    best: dict[tuple, dict] = {}
    for h in hits:
        key = (
            h.get("source") or "",
            h.get("chunk_index"),
            h.get("id") if h.get("chunk_index") is None else None,
        )
        if key not in best or _hit_key(h) > _hit_key(best[key]):
            best[key] = h
    return sorted(best.values(), key=_hit_key, reverse=True)


def _dedupe_and_merge(hits: list[dict], max_per_doc: int) -> list[dict]:
    """检索结果去重 + 相邻块合并（上下文压缩的第一层）。

    - 同一文档（source）的命中按分数降序，最多保留 `max_per_doc` 组，
      避免 Top-K 被同一章节占满，释放名额给更多文档；
    - 同一文档内 chunk_index 连续的块合并为一条（文本拼接），
      保持章节上下文完整（如标题块与其正文块）；
    - 合并后按分数全局重排，质量优先。
    """
    groups: dict[str, list[dict]] = {}
    for h in hits:
        groups.setdefault(h.get("source") or "", []).append(h)

    out: list[dict] = []
    for source, items in groups.items():
        items.sort(key=_hit_key, reverse=True)
        # 同文档同 chunk_index 的重复记录（Milvus 残留重复向量）只保留最高分
        seen_idx: set[tuple[str, object]] = set()
        unique: list[dict] = []
        for it in items:
            ci = it.get("chunk_index")
            key = (source, ci)
            if ci is not None and key in seen_idx:
                continue
            if ci is not None:
                seen_idx.add(key)
            unique.append(it)
        merged: list[dict] = []
        for it in unique:
            ci = it.get("chunk_index")
            if (
                merged
                and ci is not None
                and merged[-1].get("chunk_index") is not None
                and ci == merged[-1]["chunk_index"] + 1
            ):
                # 相邻块合并：保留高分块的其余字段，文本拼接
                merged[-1] = {
                    **merged[-1],
                    "text": merged[-1]["text"] + "\n\n" + it["text"],
                }
            else:
                merged.append(dict(it))
        out.extend(merged[:max_per_doc])
    out.sort(key=_hit_key, reverse=True)
    return out


def _dedupe_near_duplicate(hits: list[dict]) -> list[dict]:
    """指纹去重（零成本）+ 可选语义近似去重，减少喂给 LLM 的冗余。

    仅在 `settings.dedup_near_duplicate=True` 时生效（默认关 → 原样返回，行为不变）；
    开启后：
    - 指纹去重：归一化文本相同 → 只留最高分（跨源也适用，零成本）；
    - 语义去重：开启时对上一步结果再做 embedding 相似度≥阈值去重（有成本）。
    """
    if not settings.dedup_near_duplicate or not hits:
        return hits
    seen_norm: dict[str, float] = {}
    deduped: list[dict] = []
    for h in hits:
        norm = _normalize_text(h.get("text", ""))
        if not norm:
            continue
        if norm in seen_norm:
            continue
        # 指纹去重：同一文本只保留首个（已按分数降序）
        seen_norm[norm] = _hit_key(h)
        deduped.append(h)
    if len(deduped) <= 1:
        return deduped
    # 语义近似去重：两两归一化不同但 embedding 相似≥阈值 → 保留分数高者
    try:
        from app.rag.embedding import get_embedder

        embedder = get_embedder()
        texts = [h.get("text", "") for h in deduped]
        vecs = embedder.embed_texts(texts) if texts else []
        if vecs:
            keep: list[bool] = [True] * len(deduped)
            # 顺序已按分数降序，只需删除与已保留项相似的后续项
            kept_vecs: list = []
            for idx, v in enumerate(vecs if len(vecs) == len(deduped) else []):
                is_dup = False
                for kv in kept_vecs:
                    if _cosine(v, kv) >= settings.dedup_sim_threshold:
                        is_dup = True
                        break
                if is_dup:
                    keep[idx] = False
                else:
                    kept_vecs.append(v)
            deduped = [h for h, k in zip(deduped, keep) if k]
    except Exception:  # embedding 不可用/失败 → 只保留指纹去重
        pass
    return deduped


def _apply_total_budget(hits: list[dict], max_total: int) -> list[dict]:
    """总字符预算：按分数降序累计，达到预算即截断（防超长 context 喂给 LLM）。"""
    if max_total <= 0 or not hits:
        return hits
    # hits 已按分数降序（_dedupe_near_duplicate 后）
    total = 0
    out: list[dict] = []
    for h in hits:
        total += len(h.get("text", ""))
        if total > max_total:
            break
        out.append(h)
    return out
