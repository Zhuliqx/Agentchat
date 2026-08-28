"""图文双通道单元测试：mock 多模态编码器 / Milvus，不依赖真实模型。"""
from __future__ import annotations

from app.config import settings
from app.rag import vector_store
from app.rag.ingestion import _write_image_vectors
from app.rag.retriever import MilvusRetriever


def test_config_fields():
    assert settings.image_dual_channel is False
    assert settings.image_channel_top_k == 6
    assert settings.image_channel_weight == 0.4
    assert settings.image_embedding_dim == 512


def test_add_image_channel(monkeypatch):
    r = MilvusRetriever(top_k=4)
    img_hits = [
        {"source": "s.pdf", "image_index": 0, "text": "组织架构", "score": 0.9,
         "metadata": {"type": "image"}},
        {"source": "s.pdf", "image_index": 1, "text": "流程图", "score": 0.8,
         "metadata": {"type": "image"}},
    ]
    monkeypatch.setattr(r, "_search_image_channel", lambda q, k, u: img_hits)
    text_hits = [{"source": "company.md", "chunk_index": 0, "text": "正文", "rrf_score": 0.06, "score": 0.5}]

    out = r._add_image_channel("q", text_hits)
    img_out = [h for h in out if (h.get("metadata") or {}).get("type") == "image"]
    assert len(img_out) == 2
    assert img_out[0]["chunk_index"] is None           # 不与文本块 chunk_index 误并
    assert img_out[0]["id"].startswith("img:")
    assert img_out[0]["rrf_score"] > 0                 # 有可排序的 rrf 分
    assert any(h.get("source") == "company.md" for h in out)  # 文本块仍在


def test_add_image_channel_off(monkeypatch):
    # 无图像候选 → 原样返回（不改变文本结果）
    r = MilvusRetriever(top_k=4)
    monkeypatch.setattr(r, "_search_image_channel", lambda q, k, u: [])
    text_hits = [{"source": "a.md", "chunk_index": 0, "text": "x", "rrf_score": 0.05}]
    assert r._add_image_channel("q", text_hits) == text_hits


def test_write_image_vectors(monkeypatch):
    monkeypatch.setattr(settings, "image_dual_channel", True)
    monkeypatch.setattr(settings, "image_vlm_enabled", False)

    class E:
        def encode_image(self, img):
            return [0.1] * 8

    monkeypatch.setattr("app.rag.image_embedding.get_image_embedder", lambda: E())
    deleted = {}
    added: list = []
    monkeypatch.setattr(vector_store, "delete_image_by_source", lambda s, u: deleted.update(src=s, uid=u))
    monkeypatch.setattr(vector_store, "add_image_vectors", lambda recs: (added.extend(recs), recs)[1])

    n = _write_image_vectors([object(), object()], "s.pdf", "default")
    assert n == 2
    assert deleted.get("src") == "s.pdf"               # 先删旧图片向量（幂等覆盖）
    assert added[0]["embedding"] == [0.1] * 8
    assert added[0]["caption"].startswith("[图片]")     # 默认 caption 兜底
    assert added[1]["metadata"]["image_index"] == 1


def test_write_image_vectors_disabled(monkeypatch):
    monkeypatch.setattr(settings, "image_dual_channel", False)
    assert _write_image_vectors([object()], "s.pdf", "default") == 0
