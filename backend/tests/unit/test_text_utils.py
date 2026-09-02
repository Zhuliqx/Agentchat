"""通用文本抽取（extract_text / last_ai_text）单元测试。

extract_text 是 1.1 重构后全仓统一的内容抽取入口（合并了原 tools.text /
adapter / query_rewrite 三处手写语义），这里直接钉住合并后的契约。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents.tools.text import extract_text, last_ai_text


def test_extract_text_str_passthrough():
    assert extract_text("纯文本") == "纯文本"
    assert extract_text("") == ""


def test_extract_text_blocks_any_dict_with_text():
    """dict 块只要有 text 字段即抽取（不依赖 type==text，兼容 text-delta 等）。"""
    content = [
        {"type": "text", "text": "第一段"},
        {"type": "text-delta", "text": "第二段"},
        "第三段",
        {"type": "image_url", "image_url": {"url": "x"}},  # 无 text → 跳过
        {"type": "text", "text": None},                    # text 非 str → 跳过
    ]
    assert extract_text(content) == "第一段\n第二段\n第三段"


def test_extract_text_other_types_str_fallback():
    assert extract_text(123) == "123"


def test_last_ai_text_returns_last_ai_content():
    messages = [
        HumanMessage(content="问题"),
        AIMessage(content="第一段回答"),
        ToolMessage(content="工具结果", tool_call_id="c1"),
        AIMessage(content=["最终", {"type": "text", "text": "回答"}]),
    ]
    assert last_ai_text(messages) == "最终\n回答"


def test_last_ai_text_empty_when_no_ai():
    assert last_ai_text([HumanMessage(content="问题")]) == ""
    assert last_ai_text([]) == ""
