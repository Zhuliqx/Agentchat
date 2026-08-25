"""RAG 优化（自适应检索 / 意图路由 / 去重 / 总预算）单元测试。

全部使用 mock，不依赖 Milvus / Postgres / LLM / embedding 模型。
"""
from __future__ import annotations

from app.rag.retriever import (
    _apply_total_budget,
    _cosine,
    _dedupe_near_duplicate,
    _normalize_text,
)


def test_normalize_text():
    assert _normalize_text("  公司，成立于 2020年。") == "公司成立于2020年"
    assert _normalize_text("公司成立于2020年") == "公司成立于2020年"


def test_cosine():
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([], [1, 0]) == 0.0


def test_apply_total_budget():
    hits = [
        {"text": "a" * 100, "rrf_score": 0.8},
        {"text": "b" * 100, "rrf_score": 0.5},
        {"text": "c" * 100, "rrf_score": 0.2},
    ]
    # 预算 250：保留 2 条（100+100=200 ≤250，第三条会超）
    assert len(_apply_total_budget(hits, 250)) == 2
    # 预算 150：只保留第 1 条（第 2 条会超）
    assert len(_apply_total_budget(hits, 150)) == 1
    # 预算 0=不限制
    assert _apply_total_budget(hits, 0) == hits
    assert _apply_total_budget([], 100) == []


def test_dedup_near_duplicate_off(monkeypatch):
    # 关闭(默认) → 行为不变：不去重
    monkeypatch.setattr("app.config.settings.dedup_near_duplicate", False)
    hits = [
        {"text": "成立 2020", "rrf_score": 0.9},
        {"text": "成立 2020", "rrf_score": 0.8},
    ]
    assert len(_dedupe_near_duplicate(hits)) == 2


def test_dedup_near_duplicate_fingerprint_on(monkeypatch):
    # 开启 → 指纹去重（同文本只留最高分，跨源）
    monkeypatch.setattr("app.config.settings.dedup_near_duplicate", True)
    monkeypatch.setattr("app.config.settings.dedup_sim_threshold", 0.9)
    hits = [
        {"text": "成立 2020", "rrf_score": 0.9, "source": "a"},
        {"text": "成立 2020", "rrf_score": 0.8, "source": "b"},
    ]
    out = _dedupe_near_duplicate(hits)
    assert len(out) == 1 and out[0]["rrf_score"] == 0.9


def test_dedup_near_duplicate_embedding_fail(monkeypatch):
    # 开启语义去重但 embedding 抛错 → 安全降级到指纹去重，不抛
    monkeypatch.setattr("app.config.settings.dedup_near_duplicate", True)

    def _boom(_):
        raise RuntimeError("embed model unavailable")

    monkeypatch.setattr("app.rag.embedding.get_embedder", _boom)
    hits = [
        {"text": "公司成立于2020年", "rrf_score": 0.9},
        {"text": "公司成立于 2021 年", "rrf_score": 0.8},
    ]
    out = _dedupe_near_duplicate(hits)
    # 指纹不同 → 保留 2 条（embedding 失败降级）
    assert len(out) == 2


# ---------------- 意图分类 ----------------

def test_intent_classify():
    from app.rag.intent import classify, Intent

    assert classify("公司成立于哪一年") == Intent.FACT
    assert classify("帮我查一下公司的成立时间呀") == Intent.CHAT
    assert classify("请列出所有产品") == Intent.LIST
    assert classify("对比 A 和 B 产品的区别") == Intent.COMPARE
    assert classify("A 和 B 哪个好") == Intent.COMPARE
    assert classify("") == Intent.FACT


def test_intent_split_compare():
    from app.rag.intent import split_compare

    assert split_compare("A 和 B 的区别") == ["A", "B 的区别"]
    assert split_compare("A vs B 哪个好") == ["A", "B 哪个好"]


def test_get_relevant_documents_intent_vector_filter(monkeypatch):
    """意图路由 + 纯向量通道 + 非 compare intent：验证 user_filter 已绑定不 NameError。

    回归用例：修复前 user_filter 只在 compare 分支定义，fact/chat/list 走 else 分支
    在 vector 通道引用时抛 NameError。
    """
    from app.rag import vector_store
    from app.rag.retriever import MilvusRetriever

    monkeypatch.setattr("app.config.settings.intent_routing", True)
    monkeypatch.setattr("app.config.settings.hybrid_search", False)
    monkeypatch.setattr("app.config.settings.rerank_enabled", False)
    monkeypatch.setattr("app.config.settings.query_rewrite_enabled", False)
    monkeypatch.setattr("app.config.settings.dedup_near_duplicate", False)
    monkeypatch.setattr("app.config.settings.rag_max_total_chars", 0)

    def fake_search(query, top_k, score_threshold, filter_expr):
        assert filter_expr  # 关键：vector 通道必须传入 user_filter（用户隔离）
        return [{"text": "公司成立于 2020 年", "source": "company.md",
                 "chunk_index": 0, "score": 0.9}]

    monkeypatch.setattr(vector_store, "search", fake_search)
    monkeypatch.setattr(MilvusRetriever, "_user_filter", lambda self: "user_id == 'default'")

    # fact（走 else 分支）→ 触发 user_filter 引用
    docs = MilvusRetriever(user_id="default")._get_relevant_documents("公司成立于哪一年")
    assert docs and docs[0].metadata["source"] == "company.md"