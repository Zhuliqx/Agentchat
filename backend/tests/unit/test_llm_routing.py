"""LLM 路由逻辑单测（_model_name / light 回退），不依赖外部服务。"""
from __future__ import annotations

from app.agents.llm import _model_name
from app.config import settings


def test_main_model_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-chat")
    monkeypatch.setattr(settings, "llm_light_model", "")
    assert _model_name("main") == "deepseek-chat"
    # 未配置轻量模型时 light 回退主模型
    assert _model_name("light") == "deepseek-chat"


def test_light_model_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-chat")
    monkeypatch.setattr(settings, "llm_light_model", "deepseek-lite")
    assert _model_name("main") == "deepseek-chat"
    assert _model_name("light") == "deepseek-lite"


def test_dashscope_default_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "dashscope")
    monkeypatch.setattr(settings, "dashscope_model", "qwen-plus")
    monkeypatch.setattr(settings, "llm_light_model", "")
    assert _model_name("main") == "qwen-plus"
    assert _model_name("light") == "qwen-plus"


def test_chunk_hash_deterministic():
    from app.rag.chunkers import _chunk_hash

    assert _chunk_hash("abc") == _chunk_hash("abc")
    assert _chunk_hash("abc") != _chunk_hash("abd")
    assert len(_chunk_hash("abc")) == 64  # sha256 hex
