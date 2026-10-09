"""长期记忆工具单测：recall 按相关性检索（有索引）与全量回退（无索引）。"""
from __future__ import annotations


def test_recall_uses_query_when_index_available(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.agents.tools import memory_tools
    from app.config import settings

    captured: dict = {}

    class FakeStore:
        async def asearch(self, namespace, **kwargs):
            captured["namespace"] = namespace
            captured["kwargs"] = kwargs
            return [SimpleNamespace(key="k1", value={"content": "喜欢羽毛球"}, score=0.9)]

    fake_rt = SimpleNamespace(context=SimpleNamespace(user_id="alice"), store=FakeStore())
    monkeypatch.setattr(memory_tools, "get_runtime", lambda: fake_rt)
    monkeypatch.setattr(memory_tools, "store_has_index", lambda: True)

    out = asyncio.run(memory_tools.build_recall_tool().ainvoke({"query": "我有什么爱好"}))

    assert captured["namespace"] == ("alice", "memories")
    assert captured["kwargs"]["query"] == "我有什么爱好"
    assert captured["kwargs"]["limit"] == settings.memory_recall_limit
    assert "羽毛球" in out


def test_recall_falls_back_to_listing_without_index(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.agents.tools import memory_tools

    captured: dict = {}

    class FakeStore:
        async def asearch(self, namespace, **kwargs):
            captured["kwargs"] = kwargs
            return [SimpleNamespace(key="k1", value={"content": "喜欢羽毛球"})]

    fake_rt = SimpleNamespace(context=SimpleNamespace(user_id="alice"), store=FakeStore())
    monkeypatch.setattr(memory_tools, "get_runtime", lambda: fake_rt)
    monkeypatch.setattr(memory_tools, "store_has_index", lambda: False)

    asyncio.run(memory_tools.build_recall_tool().ainvoke({"query": "随便"}))

    assert "query" not in captured["kwargs"]
    assert captured["kwargs"]["limit"] == 50
