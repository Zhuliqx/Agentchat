"""流式输出装配（_PreludeDedupe / SupervisorStreamer）单元测试。

场景：supervisor 先输出开场白再调用工具，工具执行后 LLM 重新生成完整回答
（重复开场白）。流式输出按小分块到达，验证去重在各种切割下都成立。
"""
from __future__ import annotations

import asyncio

from app.agents.streaming import SupervisorStreamer, _PreludeDedupe


def _run(coro):
    return asyncio.run(coro)


class _Sink:
    """收集 on_token / on_tool_event 回调的假目标。"""

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.events: list[dict] = []

    async def on_token(self, text: str) -> None:
        self.tokens.append(text)

    async def on_tool_event(self, data: dict) -> None:
        self.events.append(data)


def _streamer(sink: _Sink) -> SupervisorStreamer:
    return SupervisorStreamer(
        on_token=sink.on_token, on_tool_event=sink.on_tool_event
    )


def test_exact_repeat_small_chunks():
    """LLM 逐字重复完整开场白，且按小分块流式到达 → 全部跳过，只留答案。"""
    prelude = "我来帮你搜索最新的AI行业新闻。"
    chunks = [
        "我来帮你",
        "搜索最新的AI行业",
        "新闻。我已经为你搜",
        "索到以下AI行业新闻要点:...",
        "（详情如下）",
    ]
    d = _PreludeDedupe(prelude)
    out = "".join(d.feed(c) for c in chunks)
    assert out == "我已经为你搜索到以下AI行业新闻要点:...（详情如下）"


def test_single_chunk_contains_full_repeat():
    """单个分块即含完整开场白（非切割）→ 也能去掉重复前缀。"""
    prelude = "我来帮你搜索最新的AI行业新闻。"
    d = _PreludeDedupe(prelude)
    out = d.feed("我来帮你搜索最新的AI行业新闻。以下是整理好的要点:...")
    assert out == "以下是整理好的要点:..."


def test_no_repeat_direct_answer():
    """工具后直接回答（不重复开场白）→ 无延迟、原样输出。"""
    prelude = "我来帮你搜索最新的AI行业新闻。"
    d = _PreludeDedupe(prelude)
    out = d.feed("以下是搜索到的结果:...")
    assert out == "以下是搜索到的结果:..."


def test_partial_rewrite_dedupes_common_prefix():
    """LLM 改写开场白（开头相同但后面不同）→ 只丢弃共同前缀。"""
    prelude = "我来帮你搜索最新的AI行业新闻。"
    d = _PreludeDedupe(prelude)
    # "我" 匹配后分歧 → 丢弃 "我"，推送 "已经..."
    out = d.feed("我已经为你搜索到以下内容:...")
    assert out == "已经为你搜索到以下内容:..."


def test_after_diverge_direct_pass():
    """分歧发生后，后续分块直接推送，不再做前缀匹配。"""
    prelude = "我来帮你搜索。"
    d = _PreludeDedupe(prelude)
    assert d.feed("我来帮") == ""           # 是前缀 → 丢弃
    assert d.feed("你查一下") == "查一下"    # "你"仍匹配前缀→丢弃，分歧后推送
    assert d.feed("再来一段") == "再来一段"  # 分歧后直接推送


def test_inactive_when_empty_expected():
    """无开场白（工具前无缓冲）→ 原样推送。"""
    d = _PreludeDedupe("")
    assert d.active is False
    assert d.feed("直接输出") == "直接输出"


# ---------------- SupervisorStreamer（开场白缓冲/工具事件装配） ----------------


def test_short_answer_buffered_until_flush():
    """短直接回答（<阈值）→ 期间不推送，flush 时一次性补推。"""
    sink = _Sink()
    st = _streamer(sink)
    _run(st.feed("好的"))
    assert sink.tokens == [] and st.answer() == ""
    _run(st.flush())
    assert sink.tokens == ["好的"]
    assert st.answer() == "好的"


def test_feed_switches_to_direct_streaming_over_threshold():
    """缓冲超过 PRELUDE_FLUSH → 判定为直接回答并开始逐字流式。"""
    sink = _Sink()
    st = _streamer(sink)
    chunk1, chunk2 = "起" * 20, "续" * 25  # 合计 45 ≥ 40
    _run(st.feed(chunk1))
    assert sink.tokens == []
    _run(st.feed(chunk2))
    assert sink.tokens == [chunk1 + chunk2]  # 超阈值一次性推缓冲
    _run(st.feed("尾"))
    assert sink.tokens == [chunk1 + chunk2, "尾"]  # 已直推，不再攒段
    assert st.answer() == chunk1 + chunk2 + "尾"


def test_emit_tool_discards_buffer_and_dedupes_answer():
    """工具事件：丢弃未流式开场白、记录 prelude、工具后去重重启、同工具不重复发事件。"""
    sink = _Sink()
    st = _streamer(sink)
    st.register_tool("rag_agent")
    prelude = "我来帮你查询公司信息"
    _run(st.feed(prelude))  # < 阈值，只进缓冲
    _run(st.emit_tool("rag_agent"))
    _run(st.emit_tool("rag_agent"))  # 同名重复 → 不再发事件
    assert st.saw_tool_call is True
    assert [e["content"] for e in sink.events] == ["工具: rag_agent"]
    _run(st.feed_answer(prelude + "：结果如下"))
    assert sink.tokens == ["：结果如下"]  # 开场白碎片已丢弃且去重
    assert st.answer() == "：结果如下"


def test_unregistered_tool_emits_nothing_and_keeps_buffer():
    """幻觉调用未注册工具 → 不发事件、不置 saw_tool_call、不破坏缓冲。"""
    sink = _Sink()
    st = _streamer(sink)
    _run(st.feed("短回答"))
    _run(st.emit_tool("phantom_tool"))  # 未登记
    assert sink.events == [] and st.saw_tool_call is False
    _run(st.flush())
    assert sink.tokens == ["短回答"]
