"""向量库一致性单元测试（增量摄入回归）。"""
from __future__ import annotations

import pytest

from app.rag import vector_store


class _FakeMilvus:
    def __init__(self) -> None:
        self.inserted: list[list[dict]] = []
        self.deleted_filters: list[str] = []

    def insert(self, collection, rows):  # noqa: ANN001
        self.inserted.append(rows)

    def delete(self, collection, filter):  # noqa: ANN001
        self.deleted_filters.append(filter)


@pytest.fixture
def fake_milvus(monkeypatch):
    fake = _FakeMilvus()
    monkeypatch.setattr(vector_store, "_client", lambda: fake)
    return fake


# ---------------- delete_by_ids ----------------

def test_delete_by_ids_filters_on_doc_id(fake_milvus):
    """必须按 doc_id 过滤：调用方传的是 Postgres documents.id。"""
    vector_store.delete_by_ids(["abc123", "def456"])
    assert len(fake_milvus.deleted_filters) == 1
    expr = fake_milvus.deleted_filters[0]
    assert expr == 'doc_id in ["abc123", "def456"]'
    # 按主键 id 过滤是 no-op（主键是独立 uuid4），绝不能退化回去
    assert not expr.startswith("id in")


def test_delete_by_ids_empty_is_noop(fake_milvus):
    vector_store.delete_by_ids([])
    assert fake_milvus.deleted_filters == []


# ---------------- add_chunks chunk_indexes ----------------

def test_add_chunks_uses_provided_chunk_indexes(fake_milvus):
    """增量摄入时 chunk_index 必须用原始块序号（而非批内位置）。"""
    chunks = [{"text": "块A", "metadata": {}}, {"text": "块B", "metadata": {}}]
    vecs = [[0.1] * 8, [0.2] * 8]
    vector_store.add_chunks(
        chunks,
        doc_ids=["d1", "d2"],
        source="/tmp/x.md",
        user_id="default",
        vectors=vecs,
        chunk_indexes=[3, 5],
    )
    assert len(fake_milvus.inserted) == 1
    rows = fake_milvus.inserted[0]
    assert [r["chunk_index"] for r in rows] == [3, 5]
    assert [r["doc_id"] for r in rows] == ["d1", "d2"]
    # 主键 id 是独立 uuid（与 Postgres id 不同）——删除必须按 doc_id 过滤
    assert rows[0]["id"] != rows[0]["doc_id"]


def test_add_chunks_default_chunk_index_is_batch_position(fake_milvus):
    """未传 chunk_indexes（全量摄入）时用批内位置，行为不变。"""
    chunks = [{"text": "块A", "metadata": {}}, {"text": "块B", "metadata": {}}]
    vecs = [[0.1] * 8, [0.2] * 8]
    vector_store.add_chunks(
        chunks,
        doc_ids=["d1", "d2"],
        source="/tmp/x.md",
        user_id="default",
        vectors=vecs,
    )
    rows = fake_milvus.inserted[0]
    assert [r["chunk_index"] for r in rows] == [0, 1]


def test_add_chunks_length_mismatch_rejected():
    with pytest.raises(ValueError):
        vector_store.add_chunks(
            [{"text": "a", "metadata": {}}],
            doc_ids=["d1", "d2"],
            source="/tmp/x.md",
            user_id="default",
            vectors=[[0.1] * 8],
        )
    with pytest.raises(ValueError):
        vector_store.add_chunks(
            [{"text": "a", "metadata": {}}],
            doc_ids=["d1"],
            source="/tmp/x.md",
            user_id="default",
            vectors=[[0.1] * 8],
            chunk_indexes=[0, 1],
        )


# ---------------- sync_chunks（幂等 upsert） ----------------

def test_sync_chunks_deletes_then_inserts(fake_milvus):
    """幂等同步：先按 doc_id 删除旧行再插入（重复执行不产生重复向量）。"""
    chunks = [{"text": "块A", "metadata": {}}, {"text": "块B", "metadata": {}}]
    vecs = [[0.1] * 8, [0.2] * 8]
    vector_store.sync_chunks(
        chunks,
        doc_ids=["d1", "d2"],
        source="/tmp/x.md",
        user_id="default",
        vectors=vecs,
        chunk_indexes=[3, 5],
    )
    # 删除按 doc_id 过滤（幂等键），而非 Milvus 主键
    assert fake_milvus.deleted_filters == ['doc_id in ["d1", "d2"]']
    rows = fake_milvus.inserted[-1]
    assert [r["doc_id"] for r in rows] == ["d1", "d2"]
    assert [r["chunk_index"] for r in rows] == [3, 5]


def test_sync_chunks_empty_doc_ids_is_noop(fake_milvus):
    """空 doc_ids：delete 与 insert 都不应触发。"""
    assert vector_store.sync_chunks([], doc_ids=[], source="s", user_id="default") == []
    assert fake_milvus.deleted_filters == []
    assert fake_milvus.inserted == []
