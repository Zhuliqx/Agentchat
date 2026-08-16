"""检索结果重排序（CrossEncoder reranker）。

在混合/向量检索的 Top-N 候选上做交叉编码精排，提升 top-k 相关性。
模型加载失败时自动降级为不重排（保持检索可用）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import settings


@lru_cache(maxsize=1)
def _get_reranker():
    from sentence_transformers import CrossEncoder

    # local_files_only：仅从本地 HF 缓存加载，避免离线环境联网 HEAD 检查卡死
    return CrossEncoder(
        settings.rerank_model,
        device=settings.embedding_device,
        local_files_only=True,
    )


def rerank(query: str, hits: list[dict[str, Any]], top_k: int | None = None) -> list[dict[str, Any]]:
    """对命中列表按 (query, text) 交叉编码重排，返回精排后的 Top-K。

    - 仅对前 rerank_candidate_k 条候选重排，控制 CPU 推理量。
    - 输入文本按 rerank_max_length 截断，减少 token。
    - 失败时原样返回前 top_k 条（降级）。
    """
    if not hits:
        return []
    top_k = top_k or settings.rerank_top_k
    candidates = hits[: settings.rerank_candidate_k]
    try:
        model = _get_reranker()
        pairs = [
            (query[: settings.rerank_max_length], h["text"][: settings.rerank_max_length])
            for h in candidates
        ]
        scores = model.predict(pairs)
        ranked = sorted(zip(candidates, scores), key=lambda x: float(x[1]), reverse=True)
        result = []
        for h, s in ranked[:top_k]:
            item = dict(h)
            item["rerank_score"] = round(float(s), 4)
            result.append(item)
        return result
    except Exception:  # 模型加载/推理失败 → 降级
        return hits[:top_k]
