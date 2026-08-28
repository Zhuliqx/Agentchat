"""图文双通道的图像多模态编码（CLIP / Chinese-CLIP）。

从 ``app.rag.embedding`` 拆出：文本嵌入与图像嵌入各自独立模块，
仅开启图文双通道（IMAGE_DUAL_CHANNEL）时才需要本模块。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


class ImageEmbedder:
    """多模态（文本 + 图像）编码器，用于图文双通道的图像向量。

    基于 HuggingFace CLIP（默认中文 Chinese-CLIP）：
    - encode_text(query)：把用户问题编码为文本向量（与图像向量同空间）；
    - encode_image(img)：把图片编码为图像向量。
    输出归一化（cosine 相似度可直接比较）。
    """

    def __init__(self, model_name: str, device: str = "cpu"):
        from transformers import (
            AutoProcessor,
            ChineseCLIPModel,
            ChineseCLIPProcessor,
            CLIPModel,
        )

        if "chinese-clip" in model_name.lower():
            self._model = ChineseCLIPModel.from_pretrained(model_name)
            self._proc = ChineseCLIPProcessor.from_pretrained(model_name)
        else:
            self._model = CLIPModel.from_pretrained(model_name)
            self._proc = AutoProcessor.from_pretrained(model_name)
        self._device = device
        self._model.to(device)  # type: ignore[reportArgumentType]
        self._model.eval()
        self.dim = int(getattr(self._model.config, "projection_dim", 0) or 0)
        if settings.image_embedding_dim and self.dim != settings.image_embedding_dim:
            logger.warning(
                "图像 embedding 维度不匹配：配置=%s 模型=%s。请同步 .env 的 IMAGE_EMBEDDING_DIM",
                settings.image_embedding_dim, self.dim,
            )

    def _torch(self, inputs: dict) -> dict:
        return {k: (v.to(self._device) if hasattr(v, "to") else v) for k, v in inputs.items()}

    def _normalize(self, vec) -> list[float]:
        import torch

        emb = vec / torch.norm(vec, dim=-1, keepdim=True)
        return emb[0].tolist()

    def encode_text(self, text: str) -> list[float]:
        import torch

        inputs = self._torch(self._proc(text=[text], return_tensors="pt", padding=True))
        with torch.no_grad():
            out = self._model.text_model(
                input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]
            )
            pooled = out.pooler_output if out.pooler_output is not None else out.last_hidden_state[:, 0]
            emb = self._model.text_projection(pooled)
        return self._normalize(emb)

    def encode_image(self, img) -> list[float]:
        import torch

        inputs = self._torch(self._proc(images=[img], return_tensors="pt"))
        with torch.no_grad():
            out = self._model.vision_model(pixel_values=inputs["pixel_values"])
            pooled = out.pooler_output if out.pooler_output is not None else out.last_hidden_state[:, 0]
            emb = self._model.visual_projection(pooled)
        return self._normalize(emb)


@lru_cache
def get_image_embedder() -> ImageEmbedder:
    """多模态编码器单例。仅 provider=local 支持；加载失败抛异常（上层降级）。"""
    return ImageEmbedder(settings.image_embedding_model, settings.resolved_embedding_device())
