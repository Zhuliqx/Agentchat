"""图片处理：从 PDF 抽取图片 + OCR 识别文字（供扫描件/截图入库）。

说明：仅提供「抽图 + OCR」这一层；VLM 语义描述在后续扩展，
当前不含；OCR 结果走现有文本通道，不改变向量 schema / 模型。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any


def extract_pdf_images(path) -> list[Any]:
    """用 PyMuPDF 抽取 PDF 每页内嵌图片，返回 PIL.Image（RGB）列表。"""
    import fitz
    import io

    from PIL import Image

    images: list[Any] = []
    try:
        with fitz.open(str(path)) as doc:
            for _pno in range(len(doc)):
                for img in doc.get_page_images(_pno):
                    xref = img[0]
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha >= 4:  # CMYK 等 → 转 RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    try:
                        data = pix.tobytes("png")
                        images.append(Image.open(io.BytesIO(data)).convert("RGB"))
                    except Exception:
                        continue
    except Exception:
        pass
    return images


@lru_cache(maxsize=1)
def _rapidocr():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _parse_rapidocr(result) -> list[str]:
    """兼容 rapidocr 1.x(tuple) / 2.x(RapidOCROutput) 的返回。"""
    if result is None:
        return []
    if isinstance(result, tuple):
        result = result[0] or []
    if hasattr(result, "txts"):  # 2.x
        return [str(t) for t in result.txts if t]
    lines = []
    for item in result or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
            lines.append(item[1])
    return lines


def ocr_text(img) -> str:
    """OCR 识别图片文字。未安装 rapidocr / 失败 → 返回空串（安全降级）。"""
    try:
        import numpy as np

        engine = _rapidocr()
        arr = np.asarray(img)
        result = engine(arr)
        return "\n".join(_parse_rapidocr(result))
    except Exception:
        return ""
