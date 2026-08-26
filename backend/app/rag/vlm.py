"""图片语义描述（VLM；②）。

对 PDF 内嵌/扫描图用视觉大模型（VLM）生成一段自然语言语义描述，
描述以「文本块」形式走现有向量通道（不改变向量 schema / 模型）。

- 默认 provider=deepseek（官方 deepseek-v4-flash-vision-exp，中文强、支持图表、便宜）；
- 任意失败降级返回 ""（不中断摄入，等效关闭该图描述）。

仅视觉模型接受图片；system/assistant 消息带图会 400，故图片只放入 user 消息。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import settings

_PROMPT = (
    "请用一句中文描述这张图片的内容。"
    "若为图表请说明横纵轴含义与主要趋势；若为截图请说明文字主题；"
    "若为示意图/流程图请说明其结构逻辑。仅描述可见内容，不要臆测；"
    "若图片为空或无法辨认，直接回答“空”。"
)

_MAX_TOKENS = 160


@lru_cache(maxsize=None)
def _endpoint() -> tuple[str, str, str]:
    """返回 (provider, base_url, api_key)。base_url/api_key 可被 image_vlm_* 显式覆盖。"""
    provider = settings.image_vlm_provider
    if settings.image_vlm_base_url and settings.image_vlm_api_key:
        return provider, settings.image_vlm_base_url, settings.image_vlm_api_key
    if provider == "dashscope":
        return provider, settings.dashscope_base_url, settings.dashscope_api_key
    if provider == "openai":
        return provider, settings.openai_base_url, settings.openai_api_key
    if provider == "ollama":
        return provider, "http://localhost:11434/v1", ""
    return provider, settings.deepseek_base_url, settings.deepseek_api_key


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI  # 延迟导入，避免未安装时报错

    _p, base_url, api_key = _endpoint()
    if not base_url or not api_key:
        raise RuntimeError("image_vlm 未配置 base_url/api_key")
    return OpenAI(base_url=base_url, api_key=api_key, timeout=60.0)


def _resize(img, max_size: int):
    """等比缩放到最长边 ≤ max_size；保持 RGB，返回副本。"""
    from PIL import Image

    if img.mode != "RGB":
        img = img.convert("RGB")
    if max_size and max(img.size) > max_size:
        img = img.copy()
        img.thumbnail((max_size, max_size))
    return img


def _encode_image(img) -> str:
    """PIL.Image → base64 data URL（JPEG，兼容性最好、体积小）。"""
    import base64
    import io

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def describe_image(img, max_size: int | None = None, detail: str | None = None) -> str:
    """用 VLM 描述一张图片。任何失败/未配置 → 返回 ""（安全降级）。"""
    try:
        client = _client()
        max_size = int(max_size if max_size is not None else settings.image_vlm_max_size)
        detail = detail or settings.image_vlm_detail
        img = _resize(img, max_size)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": _PROMPT},
            {"type": "image_url", "image_url": {"url": _encode_image(img), "detail": detail}},
        ]
        resp = client.chat.completions.create(
            model=settings.image_vlm_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=_MAX_TOKENS,
            temperature=0.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return "" if text in ("空", "图片为空") else text
    except Exception:
        return ""


def describe_images(images, max_size: int | None = None, detail: str | None = None) -> list[str]:
    """批量描述多张图；单张失败不影响其余。"""
    return [describe_image(img, max_size, detail) for img in images or []]
