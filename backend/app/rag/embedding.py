"""Embedding 封装。

支持两种 provider：
- local  : sentence-transformers 本地模型（默认 BAAI/bge-small-zh-v1.5，中文友好）
- openai : OpenAI 兼容 embedding API
统一暴露 `embed_texts()` / `embed_query()`。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


class BaseEmbedder:
    """embedding 统一接口。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class LocalEmbedder(BaseEmbedder):
    def __init__(self, model_name: str, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer

        # local_files_only：仅从本地 HF 缓存加载，避免离线环境联网 HEAD 检查卡死
        self._model = SentenceTransformer(
            model_name, device=device, local_files_only=True
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]


class OpenAIEmbedder(BaseEmbedder):
    def __init__(self, model: str, api_key: str, base_url: str):
        from langchain_openai import OpenAIEmbeddings

        self._client = OpenAIEmbeddings(
            model=model, api_key=api_key, base_url=base_url
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(text)


@lru_cache
def get_embedder() -> BaseEmbedder:
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    return LocalEmbedder(settings.embedding_model, settings.resolved_embedding_device())


class ImageEmbedder:
    """多模态（文本 + 图像）编码器，用于图文双通道的图像向量（③）。

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
        import torch

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
