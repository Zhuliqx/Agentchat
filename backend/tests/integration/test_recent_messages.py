"""get_recent_messages 顺序语义集成测试（多轮检索上下文依赖）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from helpers import postgres_available


pytestmark = pytest.mark.skipif(
    not postgres_available(), reason="Postgres 不可用（需 Docker 依赖）"
)


def test_get_recent_messages_returns_latest_in_order() -> None:
    """多轮上下文取**最近** N 条（时间正序），而非最早 N 条。"""
    from app.db.models import gen_uuid
    from app.db.postgres import (
        add_message,
        create_session,
        create_user,
        delete_session,
        delete_user,
        get_messages,
        get_recent_messages,
    )

    uid = gen_uuid()
    create_user(uid, uid)  # 自建临时用户，不依赖 init_db 预置的 guest
    s = None
    try:
        s = create_session(title="recent-test", user_id=uid)
        for i in range(10):
            add_message(s.id, "user" if i % 2 == 0 else "assistant", f"消息{i}")

        latest = get_recent_messages(s.id, 4)
        assert [m.content for m in latest] == ["消息6", "消息7", "消息8", "消息9"]

        # 对照：get_messages 取最早 N 条（历史展示语义，保持不变）
        earliest = get_messages(s.id, 4)
        assert [m.content for m in earliest] == ["消息0", "消息1", "消息2", "消息3"]
    finally:
        if s is not None:
            delete_session(s.id)
        delete_user(uid)
