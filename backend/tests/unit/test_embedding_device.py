"""EMBEDDING_DEVICE=auto 自动设备检测的单元测试。"""
from __future__ import annotations

from app.config import settings


def _set_device(monkeypatch, value: str) -> None:
    monkeypatch.setattr(settings, "embedding_device", value)


def test_explicit_cuda_passthrough(monkeypatch):
    _set_device(monkeypatch, "cuda")
    assert settings.resolved_embedding_device() == "cuda"


def test_explicit_cpu_passthrough(monkeypatch):
    _set_device(monkeypatch, "cpu")
    assert settings.resolved_embedding_device() == "cpu"


def test_auto_resolves_cuda_when_available(monkeypatch):
    _set_device(monkeypatch, "auto")
    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    assert settings.resolved_embedding_device() == "cuda"


def test_auto_resolves_cpu_when_unavailable(monkeypatch):
    _set_device(monkeypatch, "auto")
    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    assert settings.resolved_embedding_device() == "cpu"