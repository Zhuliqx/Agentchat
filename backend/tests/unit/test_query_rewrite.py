"""Query 改写（rule/llm）与 retriever 双路兜底的单元测试（纯逻辑，无 DB/LLM）。"""
from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.rag import query_rewrite as qr


def _enable(monkeypatch, mode: str, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "query_rewrite_enabled", enabled)
    monkeypatch.setattr(settings, "query_rewrite_mode", mode)
    qr._rewrite_cached.cache_clear()  # 跨用例清缓存


class _RewriteLLM:
    """记录调用次数的假改写 LLM（invoke 返回预设文本）。"""

    def __init__(self, text: str = "公司成立年份"):
        self.text = text
        self.calls = 0

    def invoke(self, prompt: str):
        self.calls += 1
        return SimpleNamespace(content=self.text)


# ---------------- rule 档 ----------------

def test_rule_strips_framing():
    q = qr._rule_rewrite("请帮我查一下公司成立于哪一年")
    assert "请" not in q and "帮我" not in q
    assert "公司" in q and "成立" in q


def test_rule_drops_tail_particle():
    q = qr._rule_rewrite("公司有多少名员工呢？")
    assert "呢" not in q and "？" not in q


def test_rule_generalize_appends_synonyms():
    q = qr._rule_rewrite("试用版多少钱")
    assert "多少钱" in q  # 原文保留（并列而非替换）
    assert "价格" in q and "费用" in q  # 同义词并列扩展


def test_rule_short_query_skips_generalize():
    assert qr._rule_rewrite("多少钱") == "多少钱"  # 短 query 不扩展


# ---------------- 开关与入口 ----------------

def test_disabled_returns_original(monkeypatch):
    _enable(monkeypatch, "rule", enabled=False)
    assert qr.rewrite_query("请帮我查公司") == "请帮我查公司"


def test_rewrite_query_honors_mode(monkeypatch):
    _enable(monkeypatch, "rule")
    q = qr.rewrite_query("请帮我查一下公司产品")
    assert "请" not in q and "帮我" not in q


# ---------------- llm 档 ----------------

def test_llm_rewrites_and_calls_once(monkeypatch):
    _enable(monkeypatch, "llm")
    fake = _RewriteLLM("公司成立年份")
    monkeypatch.setattr("app.agents.llm.get_llm", lambda kind: fake)
    assert qr.rewrite_query("公司是哪一年成立的") == "公司成立年份"
    assert fake.calls == 1


def test_llm_precise_token_exempt(monkeypatch):
    """含精确标识（数字/型号/号码）→ 跳过 LLM 改写，防精确信息被改写丢。"""
    _enable(monkeypatch, "llm")
    fake = _RewriteLLM("公司成立年份")
    monkeypatch.setattr("app.agents.llm.get_llm", lambda kind: fake)
    assert qr.rewrite_query("公司电话 010-88888888") == "公司电话 010-88888888"
    assert qr.rewrite_query("bge-reranker-base 是什么") == "bge-reranker-base 是什么"
    assert fake.calls == 0  # 精确标识 → 不调用 LLM


def test_llm_failure_falls_back(monkeypatch):
    _enable(monkeypatch, "llm")

    class Boom:
        def invoke(self, prompt):
            raise RuntimeError("timeout")

    monkeypatch.setattr("app.agents.llm.get_llm", lambda kind: Boom())
    assert qr.rewrite_query("公司成立于哪一年") == "公司成立于哪一年"


def test_llm_refusal_template_falls_back(monkeypatch):
    """LLM 未执行改写（模板回复"请提供问题"）→ 识别并回退原 query。"""
    _enable(monkeypatch, "llm")
    fake = _RewriteLLM("好的，请提供您需要改写的问题。")
    monkeypatch.setattr("app.agents.llm.get_llm", lambda kind: fake)
    assert qr.rewrite_query("公司成立于哪一年？") == "公司成立于哪一年？"


def test_llm_cache_only_calls_once(monkeypatch):
    _enable(monkeypatch, "llm")
    fake = _RewriteLLM("公司成立年份")
    monkeypatch.setattr("app.agents.llm.get_llm", lambda kind: fake)
    assert qr.rewrite_query("公司是哪一年成立的") == "公司成立年份"
    assert qr.rewrite_query("公司是哪一年成立的") == "公司成立年份"
    assert fake.calls == 1  # 缓存命中，不重复计费


# ---------------- retriever 双路兜底 ----------------

def test_retriever_dual_path(monkeypatch):
    """改写启用时：原 query + 改写结果双路检索，合并后按块去重。"""
    _enable(monkeypatch, "rule")
    monkeypatch.setattr(settings, "rerank_enabled", False)
    from app.rag import hybrid as hybrid_mod
    from app.rag.retriever import get_retriever

    calls: list[str] = []
    base = {"source": "s.md", "chunk_index": 0, "score": 0.9, "text": "内容", "id": "v1"}

    def fake_search(query, top_k, score_threshold, user_id):
        calls.append(query)
        return [dict(base, text=f"hit({query})", id=f"v-{len(calls)}")]

    monkeypatch.setattr(hybrid_mod, "search_hybrid", fake_search)
    docs = get_retriever(user_id="default").invoke("请帮我查公司产品")
    # 双路：原 query + 改写后 query
    assert len(calls) == 2
    assert calls[0] == "请帮我查公司产品"
    assert calls[1] != calls[0] and "请帮我" not in calls[1]
    assert len(docs) == 1  # 同一块（source, chunk_index）去重


def test_retriever_single_path_when_disabled(monkeypatch):
    """改写关闭时仍单路（行为不变）。"""
    _enable(monkeypatch, "rule", enabled=False)
    monkeypatch.setattr(settings, "rerank_enabled", False)
    from app.rag import hybrid as hybrid_mod
    from app.rag.retriever import get_retriever

    calls: list[str] = []
    monkeypatch.setattr(
        hybrid_mod,
        "search_hybrid",
        lambda query, top_k, score_threshold, user_id: calls.append(query) or [],
    )
    get_retriever(user_id="default").invoke("公司成立于哪一年")
    assert len(calls) == 1
