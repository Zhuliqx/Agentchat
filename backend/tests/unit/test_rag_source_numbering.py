"""检索工具的块编号：一轮多次检索时与前端来源列表（chip 顺序）保持一致。

回归场景：子 Agent 一次问答里检索了两次，第二次排序不同 -> 模型按当次上下文写的
[1] 实际指向 policies.md，而界面第 1 个来源是 company.md。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
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


def _parse(header: str) -> tuple[int, int, str]:
    """把「【来源 N｜本次第 M 位】文件名」拆成 (N, M, 文件名)。"""
    head, _, name = header.partition("】")
    num, _, rank = head.removeprefix("【来源 ").partition("｜本次第 ")
    return int(num), int(rank.removesuffix(" 位")), name


def _blocks(text: str) -> list[tuple[int, int, str]]:
    return [_parse(h) for h in _headers(text)]


def test_second_retrieval_reuses_run_level_numbering(monkeypatch):
    run_id = "test-run-tool-numbering"
    clear_rag_sources(run_id)
    # 路径统一用正斜杠：Windows 上反斜杠是分隔符、Linux 上不是，
    # 用反斜杠会让"文件名"在 CI 里变成整条路径，测试跟着平台飘
    retriever = _Retriever(
        [
            [_Doc("kb/company.md", "公司介绍"), _Doc("kb/policies.md", "数据政策")],
            [_Doc("kb/policies.md", "数据政策"), _Doc("kb/api.md", "接口说明")],
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

    assert _blocks(first) == [(1, 1, "company.md"), (2, 2, "policies.md")]
    # 第二次 policies.md 排到最前，编号仍复用 2、名次变成本次第 1；新来源接 3
    assert _blocks(second) == [(2, 1, "policies.md"), (3, 2, "api.md")]

    # 与界面来源列表（chip 顺序）一致：第二次的块正好是列表去掉第一项
    assert [name for _, _, name in _blocks(second)] == [
        Path(r["path"]).name for r in get_recent_rag_source_refs(run_id)
    ][1:]
    clear_rag_sources(run_id)


def test_without_run_id_keeps_per_call_numbering(monkeypatch):
    """没有 run_id（离线调用）时退回本次调用内的 1..N 编号，不报错也不串号。"""
    retriever = _Retriever([[_Doc("kb/a.md", "A 内容"), _Doc("kb/b.md", "B 内容")]])
    monkeypatch.setattr(rag_tool, "get_retriever", lambda user_id=None: retriever)
    monkeypatch.setattr(
        rag_tool,
        "get_runtime",
        lambda: SimpleNamespace(
            context=SimpleNamespace(user_id="default", session_id="", run_id="")
        ),
    )

    out = asyncio.run(rag_tool._build_search_knowledge_base_tool().ainvoke({"query": "x"}))

    assert _blocks(out) == [(1, 1, "a.md"), (2, 2, "b.md")]
