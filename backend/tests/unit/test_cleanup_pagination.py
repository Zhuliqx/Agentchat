"""清理逻辑单元测试：大批量删除必须分页取完，不能只删前 N 条。"""
from __future__ import annotations

import asyncio


class _PageStore:
    """模拟 LangGraph Store：每次 asearch 只返回一页，直至耗尽。"""

    def __init__(self, total: int, page: int = 1000) -> None:
        self._keys = [f"m{i:06d}" for i in range(total)]
        self._page = page
        self.deleted: list[str] = []
        self.calls: list[int] = []

    async def asearch(self, namespace, **kwargs):
        limit = kwargs.get("limit", 1000)
        batch = self._keys[: min(limit, self._page)]
        self.calls.append(len(batch))
        return [type("I", (), {"key": k, "value": {"content": "x"}})() for k in batch]

    async def adelete(self, namespace, key):
        if key in self._keys:
            self._keys.remove(key)
        self.deleted.append(key)


def test_delete_user_memories_paginates_all(monkeypatch):
    from app.api import data_ownership

    store = _PageStore(2500, page=1000)
    monkeypatch.setattr(data_ownership, "get_store", lambda: store)

    asyncio.run(data_ownership._delete_user_memories("alice"))

    assert len(store.deleted) == 2500
    assert len(set(store.deleted)) == 2500
    assert store.calls[-1] == 0  # 最终以空页结束，而不是 1000 条上限截断


def test_delete_user_vectors_paginates_all_sources(monkeypatch):
    import re

    from app.rag import vector_store

    sources = [f"/kb/doc_{i:05d}.txt" for i in range(20000)]

    class _FakeClient:
        def __init__(self, all_sources):
            self.all_sources = all_sources
            self.deleted: set[str] = set()

        def query(self, collection, filter=None, output_fields=None, limit=10000, offset=0):
            return [
                {"source": s}
                for s in self.all_sources[offset : offset + limit]
            ]

        def delete(self, collection, filter=None):
            m = re.search(r'source == "([^"]+)"', filter or "")
            if m:
                self.deleted.add(m.group(1))
            return None

    fake = _FakeClient(sources)
    monkeypatch.setattr(vector_store, "_client", lambda: fake)

    vector_store.delete_by_user("alice")

    assert len(fake.deleted) == 20000
