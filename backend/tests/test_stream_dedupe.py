"""stream_agent 工具后答案去重（_PreludeDedupe）单元测试。

场景：supervisor 先输出开场白再调用工具，工具执行后 LLM 重新生成完整回答
（重复开场白）。流式输出按小分块到达，验证去重在各种切割下都成立。
"""

from app.agents.graph import _PreludeDedupe


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
