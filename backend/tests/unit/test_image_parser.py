"""图片 OCR 解析单元测试（无 rapidocr 依赖，测降级与返回解析）。"""
from __future__ import annotations

from app.rag.image_parser import _parse_rapidocr, ocr_text


def test_parse_rapidocr_none():
    assert _parse_rapidocr(None) == []


def test_parse_rapidocr_v1_tuple():
    # 1.x: (result_list, elapse)，result = [[box, text, score], ...]
    out = _parse_rapidocr(([[[1, 2, 3, 4], "你好世界", 0.95]], 5.0))
    assert out == ["你好世界"]


def test_parse_rapidocr_v2_txts():
    class Out:
        txts = ["第一行", "第二行"]
    assert _parse_rapidocr(Out()) == ["第一行", "第二行"]


def test_ocr_text_fallback_empty(monkeypatch):
    # rapidocr 不可用 → 安全降级返回空串（不抛异常，不依赖 Pillow/numpy）
    def _boom():
        raise RuntimeError("no OCR engine")

    monkeypatch.setattr("app.rag.image_parser._rapidocr", _boom)
    assert ocr_text(object()) == ""
