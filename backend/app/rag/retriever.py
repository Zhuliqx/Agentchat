"""LangChain 兼容的检索器。

支持混合检索（向量 + BM25 + RRF）与可选 rerank 精排，
返回带 score 的 Document，供 RAG Agent 直接使用。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from langchain_core.documents import Document as LCDocument
from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.rag import hybrid, vector_store
from app.rag.rerank import rerank


def _hit_key(h: dict) -> float:
    """命中的排序分数（rerank > rrf > 向量 > bm25，取可用项）。"""
    for k in ("rerank_score", "rrf_score", "score", "bm25_score"):
        v = h.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


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


class MilvusRetriever(BaseRetriever):
    """基于 Milvus + BM25 的混合检索器（可选 rerank），限定用户知识库。"""

    top_k: int = 4
    score_threshold: float = 0.35
    user_id: str = "default"  # 知识库归属用户（隔离）
    filter_expr: Optional[str] = None

    def _user_filter(self) -> str:
        """按用户隔离的向量过滤表达式。"""
        if self.user_id:
            uid = self.user_id.replace('"', '\\"')
            return f'user_id == "{uid}"'
        return ""

    def _get_relevant_documents(self, query: str) -> list[LCDocument]:
        # 候选：rerank 开启时最多取 rerank_candidate_k 条供精排（控制 CPU 推理量）
        if settings.rerank_enabled:
            candidate_k = min(self.top_k * 3, settings.rerank_candidate_k)
        else:
            candidate_k = self.top_k
        user_filter = self._user_filter()
        if settings.hybrid_search:
            hits = hybrid.search_hybrid(
                query=query,
                top_k=candidate_k,
                score_threshold=self.score_threshold,
                user_id=self.user_id,
            )
        else:
            hits = vector_store.search(
                query=query,
                top_k=candidate_k,
                score_threshold=self.score_threshold,
                filter_expr=user_filter,
            )
        if settings.rerank_enabled:
            hits = rerank(query, hits, top_k=self.top_k)
        else:
            hits = hits[: self.top_k]

        # 上下文压缩：同文档去重 + 相邻块合并（释放名额、补全上下文）
        hits = _dedupe_and_merge(hits, settings.rag_max_per_doc)
        # 上下文压缩：超长块截断，减少噪音 token
        max_chars = settings.rag_max_chunk_chars
        if max_chars > 0:
            hits = [
                {**h, "text": h["text"][:max_chars]} if len(h["text"]) > max_chars else h
                for h in hits
            ]

        return [
            LCDocument(
                page_content=h["text"],
                metadata={
                    "source": h.get("source", ""),
                    "chunk_index": h.get("chunk_index"),
                    "score": h.get("score"),
                    "vector_id": h.get("id"),
                    "rrf_score": h.get("rrf_score"),
                    "rerank_score": h.get("rerank_score"),
                    **(h.get("metadata") or {}),
                },
            )
            for h in hits
        ]

    async def _aget_relevant_documents(self, query: str) -> list[LCDocument]:
        # 检索链路（Milvus 网络 + BM25 CPU + rerank 推理）同步且耗时，
        # 放入线程池执行，避免阻塞事件循环。
        return await asyncio.to_thread(self._get_relevant_documents, query)


def get_retriever(user_id: str = "default") -> MilvusRetriever:
    return MilvusRetriever(
        top_k=settings.rag_top_k,
        score_threshold=settings.rag_score_threshold,
        user_id=user_id,
    )
