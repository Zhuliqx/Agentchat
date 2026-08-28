"""检索结果重排序（CrossEncoder reranker）。

在混合/向量检索的 Top-N 候选上做交叉编码精排，提升 top-k 相关性。
模型加载失败时自动降级为不重排（保持检索可用）。
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from app.config import settings
from app.observability import record_retrieval_stats
from app.rag.section import section_prefix

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_reranker():
    from sentence_transformers import CrossEncoder

    # local_files_only 原因同 embedding.py：避免离线联网 HEAD 卡死
    return CrossEncoder(
        settings.rerank_model,
        device=settings.resolved_embedding_device(),
        local_files_only=True,
    )


def rerank(query: str, hits: list[dict[str, Any]], top_k: int | None = None, candidate_k: int | None = None) -> list[dict[str, Any]]:
    """对命中列表按 (query, text) 交叉编码重排，返回精排后的 Top-K。

    - 仅对前 candidate_k（默认 rerank_candidate_k）条候选重排，控制 CPU 推理量；
    - 输入文本按 rerank_max_length 截断；RERANK_SECTION_CONTEXT 开启时
      pair 文本带章节/文件名前缀（纯增量）；
    - 失败时原样返回前 top_k 条（降级）。
    """
    if not hits:
        return []
    top_k = top_k or settings.rag_top_k
    candidate_k = candidate_k or settings.rerank_candidate_k
    candidates = hits[:candidate_k]

    def _pair_text(h: dict) -> str:
        text = str(h.get("text") or "")
        if settings.rerank_section_context:
            prefix = section_prefix(h.get("metadata") or {}, h.get("source") or "")
            if prefix:
                text = f"{prefix}\n{text}"
        return text[: settings.rerank_max_length]

    try:
        _t0 = time.perf_counter()
        model = _get_reranker()
        pairs = [
            (query[: settings.rerank_max_length], _pair_text(h))
            for h in candidates
        ]
        # 批推理 batch_size 参数化（CPU 场景小 batch 减少峰值内存/预热开销）
        scores = model.predict(
            pairs,
            batch_size=settings.rerank_batch_size,
            show_progress_bar=False,
        )
        ranked = sorted(zip(candidates, scores), key=lambda x: float(x[1]), reverse=True)
        result = []
        for h, s in ranked[:top_k]:
            item = dict(h)
            item["rerank_score"] = round(float(s), 4)
            result.append(item)
        record_retrieval_stats(
            "rerank",
            {
                "candidates": len(candidates),
                "top_k": top_k,
                "scores": [round(float(s), 4) for _, s in ranked[:top_k]],
            },
            (time.perf_counter() - _t0) * 1000,
        )
        return result
    except Exception as exc:  # 模型加载/推理失败 → 降级
        logger.warning("rerank 失败，降级为未精排 Top-K: %s", exc)
        return hits[:top_k]
