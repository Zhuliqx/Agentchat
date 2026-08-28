"""文本 Embedding 封装。

支持两种 provider：
- local  : sentence-transformers 本地模型（默认 BAAI/bge-small-zh-v1.5，中文友好）
- openai : OpenAI 兼容 embedding API
统一暴露 `embed_texts()` / `embed_query()`。

图像多模态编码（图文双通道）已拆到 ``app.rag.image_embedding``。
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


@lru_cache(maxsize=1024)
def embed_query_cached(text: str) -> tuple[float, ...]:
    """查询 embedding 缓存（精确匹配）。

    评估、追问、轮询等场景会重复出现相同 query，命中缓存可跳过重复推理。
    返回 tuple（可哈希）；调用方自行 list()。仅文本通道使用；图像通道的
    多模态编码（见 app.rag.image_embedding）不在此缓存内。
    """
    return tuple(get_embedder().embed_query(text))
