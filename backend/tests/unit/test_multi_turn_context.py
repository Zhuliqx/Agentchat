"""多轮检索上下文单元测试。"""
from __future__ import annotations

from types import SimpleNamespace

from app.agents.tools import _build_retrieval_context


def _msg(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def test_empty_messages_returns_empty():
    assert _build_retrieval_context([]) == ""


def test_no_user_message_returns_empty():
    msgs = [_msg("assistant", "只有助手消息")]
    assert _build_retrieval_context(msgs) == ""


def test_single_round():
    msgs = [_msg("user", "产品A多少钱"), _msg("assistant", "199元")]
    ctx = _build_retrieval_context(msgs)
    assert "用户: 产品A多少钱" in ctx
    assert "助手: 199元" in ctx


def test_multiple_rounds_keep_order_oldest_first():
    msgs = [
        _msg("user", "第一个问题"),
        _msg("assistant", "第一个答案"),
        _msg("user", "第二个问题"),
        _msg("assistant", "第二个答案"),
    ]
    ctx = _build_retrieval_context(msgs)
    assert ctx.index("用户: 第一个问题") < ctx.index("用户: 第二个问题")


def test_max_rounds_limits_history():
    msgs = [
        _msg("user", "问题1"), _msg("assistant", "答案1"),
        _msg("user", "问题2"), _msg("assistant", "答案2"),
        _msg("user", "问题3"), _msg("assistant", "答案3"),
    ]
    ctx = _build_retrieval_context(msgs, max_rounds=2)
    # 只保留最近两轮（问题2、问题3），问题1 被裁掉
    assert "问题1" not in ctx
    assert "问题2" in ctx and "问题3" in ctx


def test_content_truncated_to_max_chars():
    long = "长" * 500
    msgs = [_msg("user", long), _msg("assistant", "ok")]
    ctx = _build_retrieval_context(msgs, max_chars=200)
    # 用户内容被截断到 200 字符
    assert "长" * 200 in ctx
    assert "长" * 201 not in ctx


def test_system_messages_skipped():
    msgs = [
        _msg("system", "系统提示"),
        _msg("user", "问题"),
        _msg("assistant", "答案"),
    ]
    ctx = _build_retrieval_context(msgs)
    assert "系统提示" not in ctx
    assert "用户: 问题" in ctx


def test_trailing_user_message_without_answer_still_used():
    # 当前问题尚未回答（用户最后一条消息）——只取前一轮的问答对
    msgs = [
        _msg("user", "问题1"),
        _msg("assistant", "答案1"),
        _msg("user", "最新问题"),
    ]
    ctx = _build_retrieval_context(msgs)
    assert "问题1" in ctx
    assert "答案1" in ctx
