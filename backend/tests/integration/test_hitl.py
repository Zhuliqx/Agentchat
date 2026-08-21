"""HITL（人工确认）中断与恢复集成测试（需 Postgres；用 FakeLLM，确定性、不依赖 LLM key）。

覆盖（补 test_api.py 缺口——resume 恢复流程 + 确定性）：
- mcp 无开关动作触发 interrupt（复用现有机制，但用 fake LLM）；
- resume=confirmed → 从断点继续，产出最终答案；
- resume=cancelled → 同样能恢复并收尾（取消分支）；
- resume 后不重复触发 interrupt（图状态正确恢复）。

DB 不可达自动跳过整个模块。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from helpers import FakeLLM, patch_llms

try:
    from sqlalchemy import text

    from app.db.postgres import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    _DB_OK = True
except Exception:
    _DB_OK = False

pytestmark = pytest.mark.skipif(
    not _DB_OK,
    reason="需要运行中的 Postgres/Milvus（请先启动 Docker 服务）",
)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _parse_sse(text: str) -> list[dict]:
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            raw = frame[5:].strip()
            if raw:
                events.append(json.loads(raw))
    return events


def _trigger_interrupt(client, monkeypatch) -> str:
    """配置 fake LLM + 强制 mcp 确认，发送消息触发 interrupt，返回 session_id。"""
    from app.agents.graph import clear_graph_cache
    from app.config import settings

    monkeypatch.setattr(settings, "hitl_enabled", True)
    monkeypatch.setattr(settings, "hitl_actions", ["mcp"])
    patch_llms(
        monkeypatch,
        supervisor=FakeLLM(
            text="确认后的最终回答",
            tool_calls=[
                {"name": "mcp_agent", "args": {"query": "测试查询"}, "id": "c1"}
            ],
        ),
        subagent=FakeLLM(text="MCP 工具返回的结果"),
    )
    clear_graph_cache()
    r = client.post(
        "/api/chat/stream",
        json={"message": "帮我查数据库", "use_rag": False, "use_search": False},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    intr = [e for e in events if e["type"] == "interrupt"]
    assert len(intr) == 1, f"mcp 无开关应触发确认，实际 {len(intr)}"
    # interrupt 阶段不应产出 message 帧
    assert not any(e["type"] == "message" for e in events), "等待确认时不产出答案"
    sid = intr[0]["data"]["session_id"]
    assert sid
    return sid


def _cleanup(client, sid: str) -> None:
    client.delete(f"/api/sessions/{sid}")


def test_hitl_resume_confirmed(client, monkeypatch):
    """interrupt 后 resume=confirmed → 从断点继续，产出最终答案。"""
    sid = _trigger_interrupt(client, monkeypatch)
    try:
        r = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "resume", "resume": "confirmed"},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        msgs = [e for e in events if e["type"] == "message"]
        assert msgs, "resume=confirmed 后应产出 message 帧"
        assert "最终回答" in msgs[0]["content"]
        # 恢复后不应再次中断
        assert not any(e["type"] == "interrupt" for e in events)
    finally:
        _cleanup(client, sid)


def test_hitl_resume_cancelled(client, monkeypatch):
    """interrupt 后 resume=cancelled → 取消分支正常收尾（不报错、不重复中断）。"""
    sid = _trigger_interrupt(client, monkeypatch)
    try:
        r = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "resume", "resume": "cancelled"},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        # 取消也应产出 message 收尾（内容是"已取消"或 supervisor 对工具结果的最终回答）
        assert any(e["type"] == "message" for e in events), "resume=cancelled 应收尾"
        assert not any(e["type"] == "interrupt" for e in events)
    finally:
        _cleanup(client, sid)


def test_hitl_no_double_interrupt_on_resume(client, monkeypatch):
    """resume 恢复后同一会话再次对话，不应误报 pending interrupt（状态已清理）。"""
    sid = _trigger_interrupt(client, monkeypatch)
    try:
        # 恢复
        r = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "resume", "resume": "confirmed"},
        )
        assert r.status_code == 200
        # 恢复后发新消息（用直接回答的 fake LLM）
        from app.agents.graph import clear_graph_cache
        from app.config import settings

        monkeypatch.setattr(
            settings, "hitl_actions", []
        )  # 恢复为自主判定模式
        clear_graph_cache()
        r2 = client.post(
            "/api/chat/stream",
            json={"session_id": sid, "message": "再聊一句", "use_rag": False},
        )
        # 不应 409（pending interrupt 已被 resume 消费）
        assert r2.status_code == 200
    finally:
        _cleanup(client, sid)

