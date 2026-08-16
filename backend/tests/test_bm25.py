"""BM25 关键词索引单元测试。"""
from __future__ import annotations

from app.rag.bm25 import BM25Index, tokenize


def test_tokenize_splits_chinese_and_english():
    tokens = tokenize("我爱Python编程和FastAPI")
    assert "爱" in tokens
    assert "python" in tokens
    assert "fastapi" in tokens
    assert "和" not in tokens  # 停用词被过滤
    assert "我" not in tokens  # 代词停用词被过滤


def test_search_ranks_relevant_docs_first():
    docs = [
        "公司成立于2020年，专注于人工智能产品研发。",
        "今天天气晴朗，适合外出郊游。",
        "人工智能技术正在改变各行各业的格局。",
    ]
    index = BM25Index(docs)
    hits = index.search("人工智能", top_k=3)
    # 两篇含"人工智能"的文档应排前，且第一篇因重复出现得分更高
    assert len(hits) >= 2
    assert hits[0][0] in (0, 2)


def test_search_empty_query_returns_empty():
    index = BM25Index(["一段内容"])
    assert index.search("的 了") == []
    assert index.search("") == []


def test_empty_corpus_safe():
    index = BM25Index([])
    assert index.search("人工智能") == []
    assert index.avgdl == 0.0
