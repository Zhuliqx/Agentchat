"""检索工具的块编号：一轮多次检索时与前端来源列表（chip 顺序）保持一致。

回归场景：子 Agent 一次问答里检索了两次，第二次排序不同 -> 模型按当次上下文写的
[1] 实际指向 policies.md，而界面第 1 个来源是 company.md。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.agents.tools.rag_tool as rag_tool
from app.agents.tools.sources import clear_rag_sources, get_recent_rag_source_refs


class _Doc:
    def __init__(self, source: str, text: str):
        self.metadata = {"source": source}
        self.page_content = text


class _Retriever:
    """按调用次序返回不同的检索结果（模拟两次检索排序不同）。"""

    def __init__(self, batches: list[list[_Doc]]):
        self.batches = list(batches)

    def invoke(self, _query: str) -> list[_Doc]:
        return self.batches.pop(0)


def _headers(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("【来源")]


def test_second_retrieval_reuses_run_level_numbering(monkeypatch):
    run_id = "test-run-tool-numbering"
    clear_rag_sources(run_id)
    retriever = _Retriever(
        [
            [_Doc("kb\\company.md", "公司介绍"), _Doc("kb\\policies.md", "数据政策")],
            [_Doc("kb\\policies.md", "数据政策"), _Doc("kb\\api.md", "接口说明")],
        ]
    )
    monkeypatch.setattr(rag_tool, "get_retriever", lambda user_id=None: retriever)
    monkeypatch.setattr(
        rag_tool,
        "get_runtime",
        lambda: SimpleNamespace(
            context=SimpleNamespace(user_id="default", session_id="", run_id=run_id)
        ),
    )

    tool = rag_tool._build_search_knowledge_base_tool()
    first = asyncio.run(tool.ainvoke({"query": "知识库有什么"}))
    second = asyncio.run(tool.ainvoke({"query": "数据怎么保留"}))

    assert _headers(first)[0].startswith("【来源 1｜本次第 1 位】company.md")
    assert _headers(first)[1].startswith("【来源 2｜本次第 2 位】policies.md")
    # 第二次 policies.md 排到最前，编号仍复用 2、名次变成本次第 1；新来源接 3
    assert _headers(second)[0].startswith("【来源 2｜本次第 1 位】policies.md")
    assert _headers(second)[1].startswith("【来源 3｜本次第 2 位】api.md")

    # 与界面来源列表（chip 顺序）一致
    assert [r["path"].split("\\")[-1] for r in get_recent_rag_source_refs(run_id)] == [
        "company.md",
        "policies.md",
        "api.md",
    ]
    clear_rag_sources(run_id)


def test_without_run_id_keeps_per_call_numbering(monkeypatch):
    """没有 run_id（离线调用）时退回本次调用内的 1..N 编号，不报错也不串号。"""
    retriever = _Retriever([[_Doc("kb\\a.md", "A 内容"), _Doc("kb\\b.md", "B 内容")]])
    monkeypatch.setattr(rag_tool, "get_retriever", lambda user_id=None: retriever)
    monkeypatch.setattr(
        rag_tool,
        "get_runtime",
        lambda: SimpleNamespace(
            context=SimpleNamespace(user_id="default", session_id="", run_id="")
        ),
    )

    out = asyncio.run(rag_tool._build_search_knowledge_base_tool().ainvoke({"query": "x"}))

    assert _headers(out)[0].startswith("【来源 1｜本次第 1 位】a.md")
    assert _headers(out)[1].startswith("【来源 2｜本次第 2 位】b.md")
