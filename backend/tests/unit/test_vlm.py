"""VLM 语义描述单元测试：mock，不依赖真实 API / 网络 / 图片。"""
from __future__ import annotations

from app.rag import vlm


class _FakeMsg:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    """记录最近一次 create 调用的 kwargs，并返回固定内容。"""

    def __init__(self, content: str) -> None:
        self.called: dict | None = None
        self._content = content

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.called = kw
        return _FakeResp(self._content)


def test_describe_image_ok(monkeypatch):
    # VLM 成功 → 返回描述文案；消息按 OpenAI 兼容结构，image_url 支持可选 detail 字段（默认 low）
    fake = _FakeClient("这是一张公司组织架构图")
    monkeypatch.setattr(vlm, "_client", lambda: fake)
    monkeypatch.setattr(vlm, "_resize", lambda img, ms: img)
    monkeypatch.setattr(vlm, "_encode_image", lambda img: "data:image/jpeg;base64,AA==")

    out = vlm.describe_image(object())
    assert out == "这是一张公司组织架构图"
    assert fake.called is not None
    msgs = fake.called["messages"]
    assert msgs[0]["role"] == "user"
    blocks = msgs[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image_url"
    assert "url" in blocks[1]["image_url"]
    assert "detail" in blocks[1]["image_url"]  # detail 可选（low/high/original/auto），默认 low


def test_describe_image_blank_returns_empty(monkeypatch):
    # 模型回答“空”→ 返回 ""（不生成无意义块）
    fake = _FakeClient("空")
    monkeypatch.setattr(vlm, "_client", lambda: fake)
    assert vlm.describe_image(object()) == ""


def test_describe_image_fallback_empty(monkeypatch):
    # 无 key / 客户端构建失败 / 调用异常 → 返回 ""（安全降级，不中断摄入）
    def boom():
        raise RuntimeError("no endpoint configured")

    monkeypatch.setattr(vlm, "_client", boom)
    assert vlm.describe_image(object()) == ""


def test_describe_images_batch(monkeypatch):
    fake = _FakeClient("图内容")
    monkeypatch.setattr(vlm, "_client", lambda: fake)
    monkeypatch.setattr(vlm, "_resize", lambda img, ms: img)
    monkeypatch.setattr(vlm, "_encode_image", lambda img: "data:image/jpeg;base64,AA==")
    out = vlm.describe_images([object(), object()])
    assert out == ["图内容", "图内容"]


def test_build_vlm_chunks_disabled(monkeypatch):
    # image_vlm_enabled=False → 不生成任何块（行为不变）
    from app.config import settings
    from app.rag.chunkers import _build_vlm_chunks

    monkeypatch.setattr(settings, "image_vlm_enabled", False)
    assert _build_vlm_chunks([object()], "src") == []


def test_build_vlm_chunks_enabled(monkeypatch):
    # 开启后：describle_image 返回描述 → 生成 kind=image_vlm 块；空描述跳过
    from app.config import settings
    from app.rag.chunkers import _build_vlm_chunks

    monkeypatch.setattr(settings, "image_vlm_enabled", True)
    calls = {"n": 0}

    def fake_describe(img):
        calls["n"] += 1
        return "示意图" if calls["n"] == 1 else ""

    monkeypatch.setattr(vlm, "describe_image", fake_describe)

    chunks = _build_vlm_chunks([object(), object()], "src")
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("[图片描述]")
    assert chunks[0]["metadata"]["kind"] == "image_vlm"
    assert chunks[0]["metadata"]["image_index"] == 0
