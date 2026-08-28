"""RAG 上下文增强单元测试。"""
from __future__ import annotations

from app.agents.tools import _front_load
from app.rag.embedding import embed_query_cached
from app.rag.ingestion import _embed_context_text
from app.rag.section import section_prefix


# ---------------- 上下文嵌入增强 ----------------

def test_embed_context_text_uses_headers():
    chunk = {"text": "正文", "metadata": {"H1": "公司介绍", "H2": "产品线"}}
    out = _embed_context_text(chunk, "/tmp/kb/company.md")
    assert out.startswith("[章节] 公司介绍 > 产品线\n正文")


def test_embed_context_text_partial_headers():
    chunk = {"text": "正文", "metadata": {"H1": "产品"}}
    out = _embed_context_text(chunk, "/tmp/kb/company.md")
    assert out == "[章节] 产品\n正文"


def test_embed_context_text_fallback_to_filename():
    chunk = {"text": "正文", "metadata": {}}
    out = _embed_context_text(chunk, "/tmp/kb/手册.pdf")
    assert out == "[文档] 手册.pdf\n正文"


def test_embed_context_text_empty_text_safe():
    out = _embed_context_text({"text": "", "metadata": {}}, "/tmp/a.txt")
    assert out == "[文档] a.txt\n"


# ---------------- 章节前缀（嵌入与 rerank 共用） ----------------

def test_section_prefix_from_metadata():
    assert section_prefix({"H1": "公司介绍"}, "/tmp/kb/company.md") == "[章节] 公司介绍"


def test_section_prefix_fallback_to_source():
    assert section_prefix({}, "/tmp/kb/company.md") == "[文档] company.md"


def test_section_prefix_empty():
    assert section_prefix({}, "") == ""


def test_section_prefix_ignores_blank_headers():
    assert section_prefix({"H1": "", "H2": "  "}, "/tmp/kb/a.md") == "[文档] a.md"


# ---------------- 相关块前置 ----------------

def _docs(n: int) -> list:
    return [f"doc{i}" for i in range(n)]


def test_front_load_short_lists_unchanged():
    assert _front_load(_docs(1)) == ["doc0"]
    assert _front_load(_docs(2)) == ["doc0", "doc1"]


def test_front_load_three():
    # [best] + 其余(原序) + [second_best]
    assert _front_load(_docs(3)) == ["doc0", "doc2", "doc1"]


def test_front_load_five():
    assert _front_load(_docs(5)) == ["doc0", "doc2", "doc3", "doc4", "doc1"]


def test_front_load_preserves_elements():
    out = _front_load(_docs(5))
    assert sorted(out) == _docs(5)


# ---------------- 查询 embedding 缓存 ----------------

def test_embed_query_cached_hits(monkeypatch):
    embed_query_cached.cache_clear()
    calls = {"n": 0}

    class _Fake:
        def embed_query(self, text):  # noqa: ANN001
            calls["n"] += 1
            return [0.1, 0.2, float(len(text))]  # 不同文本 → 不同向量

    monkeypatch.setattr("app.rag.embedding.get_embedder", lambda: _Fake())
    a = embed_query_cached("缓存测试问题")
    b = embed_query_cached("缓存测试问题")
    c = embed_query_cached("不同的问题")
    assert a == b
    assert calls["n"] == 2  # 相同 query 只嵌入一次
    assert c != a
