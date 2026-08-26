"""LangChain 兼容的检索器。

支持混合检索（向量 + BM25 + RRF）与可选 rerank 精排，
返回带 score 的 Document，供 RAG Agent 直接使用。
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from langchain_core.documents import Document as LCDocument
from langchain_core.retrievers import BaseRetriever
from pydantic import PrivateAttr

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


def _normalize_text(text: str) -> str:
    """文本归一化：去空白/标点/低权重符号，用于指纹去重判重。"""
    return re.sub(r"\s+|[，。、；：？！,.?!:;\"'、\-_]", "", text or "").lower()


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


class MilvusRetriever(BaseRetriever):
    """基于 Milvus + BM25 的混合检索器（可选 rerank），限定用户知识库。"""

    top_k: int = 4
    score_threshold: float = 0.35
    user_id: str = "default"  # 知识库归属用户（隔离）
    filter_expr: Optional[str] = None
    # 本查询的图像通道候选（③，用于结果保底，避免被 rerank 用弱 caption 降权剔除）
    _img_hits: list[dict] = PrivateAttr(default_factory=list)

    def _user_filter(self) -> str:
        """按用户隔离的向量过滤表达式。"""
        return vector_store.user_filter_expr(self.user_id)

    def _get_relevant_documents(self, query: str) -> list[LCDocument]:
        # 意图路由：按 query 类型调整 top_k / threshold / 是否改写 / 是否拆多路
        if settings.intent_routing:
            from app.rag.intent import classify, split_compare

            intent = classify(query)
            # 用户隔离过滤表达式：所有子分支（fact/chat/list/compare）在向量通道
            # 都需要它（filter_expr），故在此统一绑定，避免仅 compare 定义导致 NameError。
            user_filter = self._user_filter()
            top_k = self.top_k
            threshold = self.score_threshold
            # 候选：rerank 开启时最多取 rerank_candidate_k 条供精排（控制 CPU 推理量）
            if settings.rerank_enabled:
                candidate_k = min(top_k * 3, settings.adaptive_candidate_k if intent == "chat" else settings.rerank_candidate_k)
            else:
                candidate_k = top_k
            if intent == "list":
                top_k = max(top_k, 6); threshold = max(threshold - 0.1, 0.1)  # 放宽
            elif intent == "compare":
                # 对比类：拆多子查询分别检索再合并
                queries = split_compare(query) or [query]
                all_hits: list[dict] = []
                for sub in queries:
                    for q in ([sub] if not settings.query_rewrite_enabled else _expand_queries(sub)):
                        all_hits.extend(
                            hybrid.search_hybrid(query=q, top_k=candidate_k,
                                                 score_threshold=threshold, user_id=self.user_id)
                            if settings.hybrid_search else
                            vector_store.search(query=q, top_k=candidate_k, score_threshold=threshold,
                                                filter_expr=user_filter)
                        )
                hits = _merge_multi(all_hits)
                return self._finalize(query, hits, top_k)
            else:
                queries = _expand_queries(query)
                all_hits: list[dict] = []
                for q in queries:
                    all_hits.extend(
                        hybrid.search_hybrid(query=q, top_k=candidate_k,
                                             score_threshold=threshold, user_id=self.user_id)
                        if settings.hybrid_search else
                        vector_store.search(query=q, top_k=candidate_k, score_threshold=threshold,
                                            filter_expr=user_filter)
                    )
                hits = _merge_multi(all_hits)
                return self._finalize(query, hits, top_k)
        # ---------- 非意图路由：现有行为 + 低置信自适应 ----------
        # 候选：rerank 开启时最多取 rerank_candidate_k 条供精排（控制 CPU 推理量）
        if settings.rerank_enabled:
            candidate_k = min(self.top_k * 3, settings.rerank_candidate_k)
        else:
            candidate_k = self.top_k
        user_filter = self._user_filter()
        if settings.adaptive_retrieval:
            # 自适应：先单路初检索算置信；低置信 → 放宽候选 + 触发改写双路二次检索
            first = self._search_queries([query], candidate_k, user_filter)
            if _best_score(first) < settings.conf_trigger_threshold:
                widen_k = max(candidate_k, settings.adaptive_candidate_k)
                queries = _expand_queries(query)  # 原 query + 改写（改写未启用则单路）
                hits = _merge_multi(self._search_queries(queries, widen_k, user_filter))
                return self._finalize(query, hits, self.top_k)
            return self._finalize(query, first, self.top_k)
        # 查询改写：原 query + 改写结果双路检索（未启用时单路，行为不变）
        queries = _expand_queries(query)
        all_hits: list[dict] = []
        for q in queries:
            all_hits.extend(
                hybrid.search_hybrid(
                    query=q,
                    top_k=candidate_k,
                    score_threshold=self.score_threshold,
                    user_id=self.user_id,
                )
                if settings.hybrid_search
                else vector_store.search(
                    query=q,
                    top_k=candidate_k,
                    score_threshold=self.score_threshold,
                    filter_expr=user_filter,
                )
            )
        hits = _merge_multi(all_hits)
        return self._finalize(query, hits, self.top_k)

    def _search_queries(
        self, queries: list[str], top_k: int, user_filter: str
    ) -> list[dict]:
        """对一组 query 分别检索并合并（供自适应/意图路由复用）。"""
        all_hits: list[dict] = []
        for q in queries:
            if settings.hybrid_search:
                all_hits.extend(
                    hybrid.search_hybrid(
                        query=q, top_k=top_k,
                        score_threshold=self.score_threshold, user_id=self.user_id,
                    )
                )
            else:
                all_hits.extend(
                    vector_store.search(
                        query=q, top_k=top_k,
                        score_threshold=self.score_threshold, filter_expr=user_filter,
                    )
                )
        return _merge_multi(all_hits)

    def _finalize(self, query: str, hits: list[dict], top_k: int) -> list[LCDocument]:
        """公共收尾：精排 / 去重 / 预算 / 截断 → Document 列表。"""
        # 图文双通道（③）：并入图像通道候选（默认关=行为不变）
        img_on = settings.image_dual_channel
        if img_on:
            hits = self._add_image_channel(query, hits)
        if settings.rerank_enabled:
            # 精排固定用原 query：跨路/改写 query 的 rerank 分数不可比，且短改写
            # query 精排实测不稳（见 _expand_queries 注释）；改写只扩召回。
            cand = settings.rerank_candidate_k + (settings.image_channel_top_k if img_on else 0)
            hits = rerank(query, hits, top_k=top_k, candidate_k=cand)
        else:
            hits = hits[:top_k]
        # 上下文压缩：同文档去重 + 相邻块合并（释放名额、补全上下文）
        hits = _dedupe_and_merge(hits, settings.rag_max_per_doc)
        # RAG 优化：指纹/语义近似去重（减少冗余）+ 总字符预算
        hits = _dedupe_near_duplicate(hits)
        hits = _apply_total_budget(hits, settings.rag_max_total_chars)
        # 上下文压缩：超长块截断，减少噪音 token
        max_chars = settings.rag_max_chunk_chars
        if max_chars > 0:
            hits = [
                {**h, "text": h["text"][:max_chars]} if len(h["text"]) > max_chars else h
                for h in hits
            ]
        # ③ 图像通道保底：相关图（分数≥阈值）强制进入最终结果，避免被弱 caption rerank 剔除
        if img_on and self._img_hits:
            guard = [
                dict(h) for h in self._img_hits
                if float(h.get("score") or 0) >= 0.30
            ][:2]
            if guard:
                seen = {(h.get("source"), h.get("image_index")) for h in hits}
                guard = [g for g in guard if (g.get("source"), g.get("image_index")) not in seen]
                hits = guard + hits
        hits = hits[:top_k]
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

    def _search_image_channel(self, query: str, top_k: int, user_id: str) -> list[dict]:
        """图像通道检索：用多模态文本编码器编码 query → 查图片 collection。失败/无模型 → []。"""
        try:
            from app.rag.embedding import get_image_embedder

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
