"""宿主适配器单测：source→开关映射、executor 包装、图构建缓存。"""
from __future__ import annotations

import asyncio

from task_agent.executor import ExecuteRequest

from app.agents import task_agent_adapter as adapter


def test_source_route_covers_all_sources():
    for src in ("kb", "db", "web", "code", "default"):
        assert src in adapter._SOURCE_ROUTE


def test_host_executor_maps_source_to_flags(monkeypatch):
    calls: list[dict] = []

    async def fake_run_agent(**kwargs):
        calls.append(kwargs)
        return {"answer": "查询结果"}

    monkeypatch.setattr(adapter, "run_agent", fake_run_agent)
    executor = adapter._HostExecutor()

    async def go():
        r1 = await executor(ExecuteRequest(action="公司成立年份", source="kb"))
        r2 = await executor(ExecuteRequest(action="1到100质数和", source="code"))
        assert r1.answer == "查询结果" and r2.answer == "查询结果"

    asyncio.run(go())
    assert calls[0]["use_rag"] is True and calls[0]["use_search"] is False
    assert calls[0]["question"] == "请用知识库查询：公司成立年份"
    assert calls[1]["use_rag"] is False and calls[1]["use_search"] is False
    assert calls[1]["question"] == "请用代码计算：1到100质数和"


def test_build_host_task_agent_caches_by_config(monkeypatch):
    a = adapter.build_host_task_agent()
    b = adapter.build_host_task_agent()
    assert a is b  # 相同配置命中缓存
    monkeypatch.setattr("app.config.settings.task_agent_mode", "fixed")
    c = adapter.build_host_task_agent()
    assert c is not a  # 模式变化 → 重建
