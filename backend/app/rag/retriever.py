"""LangChain 兼容的检索器。

支持混合检索（向量 + BM25 + RRF）与可选 rerank 精排，
返回带 score 的 Document，供 RAG Agent 直接使用。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from langchain_core.documents import Document as LCDocument
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

from app.config import settings
from app.rag import hybrid, vector_store
from app.rag.postprocess import (
    _apply_total_budget,
    _best_score,
    _dedupe_and_merge,
    _dedupe_near_duplicate,
    _merge_multi,
)
from app.rag.rerank import rerank


def _expand_queries(query: str) -> list[str]:
    """改写 + 原 query 双路检索兜底：改写丢了信息时原句还能召回。

    改写未启用、改写为空、或改写结果与原句相同 → 单路（行为不变）。
    精排固定用原 query：短改写 query 精排不稳，改写只承担扩召回。
    """
    if not settings.query_rewrite_enabled:
        return [query]
    from app.rag.query_rewrite import rewrite_query  # 延迟导入防环

    rewritten = rewrite_query(query)
    if not rewritten or rewritten == query:
        return [query]
    return [query, rewritten]


class MilvusRetriever(BaseRetriever):
    """基于 Milvus + BM25 的混合检索器（可选 rerank），限定用户知识库。"""

    top_k: int = 4
    score_threshold: float = 0.35
    user_id: str = "default"  # 知识库归属用户（隔离）
    filter_expr: Optional[str] = None
    # 本查询的图像通道候选（_finalize 中用于保底，见下）
    _img_hits: list[dict] = PrivateAttr(default_factory=list)

    def _user_filter(self) -> str:
        """按用户隔离的向量过滤表达式。"""
        return vector_store.user_filter_expr(self.user_id)

    def _get_relevant_documents(self, query: str) -> list[LCDocument]:
        # 意图路由：按 query 类型调整 top_k / threshold / 是否改写 / 是否拆多路
        if settings.intent_routing:
            from app.rag.intent import classify, split_compare

            intent = classify(query)
            user_filter = self._user_filter()
            # 候选：rerank 开启时最多取 rerank_candidate_k 条供精排（控制 CPU 推理量）
            candidate_k = (
                min(self.top_k * 3, settings.adaptive_candidate_k if intent == "chat" else settings.rerank_candidate_k)
                if settings.rerank_enabled else self.top_k
            )
            top_k = self.top_k
            threshold = self.score_threshold
            if intent == "list":
                top_k = max(top_k, 6)
                threshold = max(threshold - 0.1, 0.1)  # 放宽
            elif intent == "compare":
                # 对比类：拆多子查询分别检索再合并
                all_hits: list[dict] = []
                for sub in split_compare(query) or [query]:
                    subs = [sub] if not settings.query_rewrite_enabled else _expand_queries(sub)
                    all_hits.extend(self._search_queries(subs, candidate_k, user_filter, threshold))
                return self._finalize(query, _merge_multi(all_hits), top_k)
            else:
                hits = self._search_queries(_expand_queries(query), candidate_k, user_filter, threshold)
                return self._finalize(query, hits, top_k)
        # 非意图路由：候选受 rerank_candidate_k 限制（控制 CPU 推理量）
        candidate_k = (
            min(self.top_k * 3, settings.rerank_candidate_k)
            if settings.rerank_enabled else self.top_k
        )
        user_filter = self._user_filter()
        if settings.adaptive_retrieval:
            # 自适应：先单路初检索算置信；低置信 → 放宽候选 + 改写双路二次检索
            first = self._search_queries([query], candidate_k, user_filter)
            if _best_score(first) < settings.conf_trigger_threshold:
                widen_k = max(candidate_k, settings.adaptive_candidate_k)
                hits = self._search_queries(_expand_queries(query), widen_k, user_filter)
                return self._finalize(query, hits, self.top_k)
            return self._finalize(query, first, self.top_k)
        # 查询改写：原 query + 改写结果双路检索（未启用时单路，行为不变）
        hits = self._search_queries(_expand_queries(query), candidate_k, user_filter)
        return self._finalize(query, hits, self.top_k)

    def _search_queries(
        self, queries: list[str], top_k: int, user_filter: str, threshold: float | None = None
    ) -> list[dict]:
        """对一组 query 分别检索并合并（供意图路由/自适应/改写双路复用）。"""
        threshold = self.score_threshold if threshold is None else threshold
        all_hits: list[dict] = []
        for q in queries:
            if settings.hybrid_search:
                all_hits.extend(
                    hybrid.search_hybrid(
                        query=q, top_k=top_k,
                        score_threshold=threshold, user_id=self.user_id,
                    )
                )
            else:
                all_hits.extend(
                    vector_store.search(
                        query=q, top_k=top_k,
                        score_threshold=threshold, filter_expr=user_filter,
                    )
                )
        return _merge_multi(all_hits)

    def _finalize(self, query: str, hits: list[dict], top_k: int) -> list[LCDocument]:
        """公共收尾：精排 / 去重 / 预算 / 截断 → Document 列表。"""
        img_on = settings.image_dual_channel  # 图文双通道（默认关）
        if img_on:
            hits = self._add_image_channel(query, hits)
        if settings.rerank_enabled:
            # 精排固定用原 query：跨路/改写 query 的 rerank 分数不可比，且短改写
            # query 精排实测不稳（见 _expand_queries 注释）；改写只扩召回。
            cand = settings.rerank_candidate_k + (settings.image_channel_top_k if img_on else 0)
            hits = rerank(query, hits, top_k=top_k, candidate_k=cand)
        else:
            hits = hits[:top_k]
        # 上下文压缩管线：去重合并 → 近似去重 → 总字符预算 → 块截断
        hits = _dedupe_and_merge(hits, settings.rag_max_per_doc)
        hits = _dedupe_near_duplicate(hits)
        hits = _apply_total_budget(hits, settings.rag_max_total_chars)
        max_chars = settings.rag_max_chunk_chars
        if max_chars > 0:
            hits = [
                {**h, "text": h["text"][:max_chars]} if len(h["text"]) > max_chars else h
                for h in hits
            ]
        # 图像通道保底：相关图（分数≥阈值）强制进入最终结果，避免被弱 caption rerank 剔除
        if img_on and self._img_hits:
            guard = [
                dict(h) for h in self._img_hits
                if float(h.get("score") or 0) >= 0.30
            ][:2]
            if guard:
                guard_keys = {(g.get("source"), g.get("image_index")) for g in guard}
                # 排序调和：相关图(≥阈值)强制前置到最前，并移除同位置旧命中(如 VLM 文本块)，
                # 使图片命中稳定占据首名(Hit@1)，而非被文本块挤到后位。
                hits = [h for h in hits if (h.get("source"), h.get("image_index")) not in guard_keys]
                hits = guard + hits
        hits = hits[:top_k]
        return [
            LCDocument(
                page_content=h["text"],
                metadata={
                    "source": h.get("source", ""),
                    "chunk_index": h.get("chunk_index"),
                    "image_index": h.get("image_index"),
                    "score": h.get("score"),
                    "vector_id": h.get("id"),
                    "rrf_score": h.get("rrf_score"),
                    "rerank_score": h.get("rerank_score"),
                    **(h.get("metadata") or {}),
                },
            )
            for h in hits
        ]

    def _search_image_channel(self, query: str, top_k: int, user_id: str) -> list[dict]:
        """图像通道检索：用多模态文本编码器编码 query → 查图片 collection。失败/无模型 → []。"""
        try:
            from app.rag.image_embedding import get_image_embedder

            qvec = get_image_embedder().encode_text(query)
        except Exception:
            return []
        return vector_store.search_image(qvec, top_k=top_k, user_id=user_id)

    def _add_image_channel(self, query: str, hits: list[dict]) -> list[dict]:
        """并入图像通道候选：图片候选赋 rrf_score（可与文本 rrf 融合），前置到候选集。"""
        img_hits = self._search_image_channel(query, settings.image_channel_top_k, self.user_id)
        self._img_hits = img_hits
        if not img_hits:
            return hits
        weight = max(0.0, float(settings.image_channel_weight))
        merged: list[dict] = []
        for i, ih in enumerate(img_hits):
            ih = dict(ih)
            ih["chunk_index"] = None                    # 避免与文本块的 chunk_index 误并
            ih["id"] = f"img:{ih.get('source')}:{ih.get('image_index')}"
            ih["rrf_score"] = round((1.0 / (settings.rrf_k + (i + 1))) * weight, 6)
            merged.append(ih)
        return _merge_multi(merged + hits)

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
