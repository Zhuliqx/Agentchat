"""图片语义描述（VLM）。

对 PDF 内嵌/扫描图用视觉大模型（VLM）生成一段自然语言语义描述，
描述以「文本块」形式走现有向量通道（不改变向量 schema / 模型）。

- 默认 provider=deepseek
- 任意失败降级返回 ""（不中断摄入，等效关闭该图描述）。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from openai.types.chat import (
    ChatCompletionContentPartParam,
    ChatCompletionUserMessageParam,
)

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
    """用 VLM 描述一张图片。任何失败/未配置 → 返回 ""（安全降级）。

    DeepSeek 等视觉端点对同一请求**偶发返回空 content**（不抛异常），故对"
    空响应"做有限重试兜底，才保证描述稳定可拿到。
    """
    last_text = ""
    for _ in range(3):
        try:
            client = _client()
            max_size = int(max_size if max_size is not None else settings.image_vlm_max_size)
            detail = detail or settings.image_vlm_detail
            img = _resize(img, max_size)
            image_url: dict[str, Any] = {"url": _encode_image(img)}
            if detail:  # 部分兼容端点（如 DeepSeek）不接受 OpenAI 特有 detail 字段，为空则省略
                image_url["detail"] = detail
            content: list[ChatCompletionContentPartParam] = [
                {"type": "text", "text": _PROMPT},
                cast(
                    ChatCompletionContentPartParam,
                    {"type": "image_url", "image_url": image_url},
                ),
            ]
            messages: list[ChatCompletionUserMessageParam] = [
                {"role": "user", "content": content}
            ]
            resp = client.chat.completions.create(
                model=settings.image_vlm_model,
                messages=messages,
                temperature=0.0,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text and text not in ("空", "图片为空"):
                return text
            last_text = text
        except Exception:
            continue
    return last_text


def describe_images(images, max_size: int | None = None, detail: str | None = None) -> list[str]:
    """批量描述多张图；单张失败不影响其余。"""
    return [describe_image(img, max_size, detail) for img in images or []]
