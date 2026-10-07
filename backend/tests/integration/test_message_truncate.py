"""消息截断接口：从指定消息起原子删除（编辑重发用），越权/跨会话拒绝。

回归场景：以前编辑重发是前端逐条 DELETE，任一步失败就留下半截历史；
现在由服务端一个事务删「该消息及其之后」的全部消息。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import db_available

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytestmark = pytest.mark.skipif(
    not db_available(), reason="需要运行中的 Postgres（请先启动 Docker 服务）"
)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_truncate_removes_target_and_following(client: TestClient) -> None:
    from app.db import postgres

    sid = client.post("/api/sessions").json()["id"]
    try:
        first = postgres.add_message(sid, "user", "第一问")
        second = postgres.add_message(sid, "assistant", "第一答")
        postgres.add_message(sid, "user", "第二问")
        postgres.add_message(sid, "assistant", "第二答")

        r = client.post(f"/api/sessions/{sid}/truncate", json={"message_id": second.id})
        assert r.status_code == 200
        assert r.json() == {"deleted": 3}

        left = client.get(f"/api/sessions/{sid}").json()
        assert [m["id"] for m in left] == [first.id]
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_truncate_rejects_message_from_other_session(client: TestClient) -> None:
    from app.db import postgres

    sid_a = client.post("/api/sessions").json()["id"]
    sid_b = client.post("/api/sessions").json()["id"]
    try:
        postgres.add_message(sid_a, "user", "A 的问题")
        foreign = postgres.add_message(sid_b, "user", "B 的问题")

        r = client.post(f"/api/sessions/{sid_a}/truncate", json={"message_id": foreign.id})
        assert r.status_code == 404
        # 两个会话都不该被误删
        assert len(client.get(f"/api/sessions/{sid_a}").json()) == 1
        assert len(client.get(f"/api/sessions/{sid_b}").json()) == 1
    finally:
        client.delete(f"/api/sessions/{sid_a}")
        client.delete(f"/api/sessions/{sid_b}")


def test_truncate_resets_graph_state(client: TestClient) -> None:
    """截断必须同时重置图状态，否则模型仍记得被删掉的对话（含截空场景）。"""
    import asyncio

    from app.agents.graph import reset_thread_messages, thread_messages
    from app.db import postgres

    sid = client.post("/api/sessions").json()["id"]
    try:
        postgres.add_message(sid, "user", "问题一")
        postgres.add_message(sid, "assistant", "回答一")
        second = postgres.add_message(sid, "user", "问题二")
        postgres.add_message(sid, "assistant", "回答二")
        asyncio.run(
            reset_thread_messages(
                sid,
                [
                    ("user", "问题一"),
                    ("assistant", "回答一"),
                    ("user", "问题二"),
                    ("assistant", "回答二"),
                ],
            )
        )
        assert len(asyncio.run(thread_messages(sid))) == 4

        r = client.post(f"/api/sessions/{sid}/truncate", json={"message_id": second.id})
        assert r.json() == {"deleted": 2}

        left = asyncio.run(thread_messages(sid))
        assert [m.content for m in left] == ["问题一", "回答一"]

        # 截空：全部删掉后状态里不应残留任何消息
        first = client.get(f"/api/sessions/{sid}").json()[0]
        emptied = client.post(f"/api/sessions/{sid}/truncate", json={"message_id": first["id"]})
        assert emptied.json() == {"deleted": 2}
        assert asyncio.run(thread_messages(sid)) == []
    finally:
        client.delete(f"/api/sessions/{sid}")
