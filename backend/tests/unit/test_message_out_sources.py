"""历史接口的 sources 兼容性：新记录是 {path, hits}，老记录是路径字符串。

回归场景：启用命中片段数后，落库的 sources 变成对象列表，但 MessageOut 仍声明
为 list[str] → 任何新生成的会话读历史都会 500。
"""
from __future__ import annotations

from app.api.routes.sessions import MessageOut


def test_accepts_source_refs_and_legacy_strings():
    msg = MessageOut(
        id="m1",
        role="assistant",
        content="回答",
        sources=[
            {"path": "D:/kb/company.md", "hits": 2},
            "D:/kb/legacy.md",
        ],
        created_at="2026-09-13T16:00:00+00:00",
    )

    dumped = msg.model_dump()
    assert dumped["sources"][0] == {"path": "D:/kb/company.md", "hits": 2}
    assert dumped["sources"][1] == "D:/kb/legacy.md"


def test_sources_default_to_empty_list():
    assert MessageOut(id="m1", role="user", content="问题").sources == []
