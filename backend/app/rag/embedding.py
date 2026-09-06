"""文本 Embedding 封装。

支持两种 provider：
- local  : sentence-transformers 本地模型（默认 BAAI/bge-small-zh-v1.5，中文友好）
- openai : OpenAI 兼容 embedding API
统一暴露 `embed_texts()` / `embed_query()`。

图像多模态编码（图文双通道）已拆到 ``app.rag.image_embedding``。
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

from app.cache.redis_client import get_redis, redis_key
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

        # local_files_only：跟随 settings.hf_offline（默认离线；CI 设 HF_OFFLINE=false 时允许联网下载）
        self._model = SentenceTransformer(
            model_name, device=device, local_files_only=settings.hf_offline
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


def _embedding_cache_key(text: str) -> str:
    """embedding 缓存 key：模型身份与文本哈希，key 中不含原文。"""
    identity = f"{settings.embedding_provider}|{settings.embedding_model}"
    digest = hashlib.sha256(
        f"{identity}\0{text}".encode("utf-8")
    ).hexdigest()
    return redis_key("cache", "embed", "v1", digest)


def _encode_vector(vector) -> str:
    return json.dumps([float(x) for x in vector])


def _read_embedding_cache(key: str) -> list[float] | None:
    if not settings.embedding_cache_enabled:
        return None
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None  # Redis 故障/脏数据按 miss 处理，不影响调用方


def _write_embedding_cache(key: str, vector) -> None:
    if not settings.embedding_cache_enabled:
        return
    client = get_redis()
    if client is None:
        return
    try:
        client.set(
            key,
            _encode_vector(vector),
            ex=int(settings.embedding_cache_ttl_seconds),
        )
    except Exception:  # noqa: BLE001
        pass  # 写失败只损失缓存收益


@lru_cache(maxsize=1024)
def embed_query_cached(text: str) -> tuple[float, ...]:
    """查询 embedding 缓存：进程内 lru 为 L1，Redis 为跨 worker 的 L2。

    评估、追问、轮询等场景会重复出现相同 query，命中缓存可跳过重复推理。
    返回 tuple（可哈希）；调用方自行 list()。仅文本通道使用；图像通道的
    多模态编码（见 app.rag.image_embedding）不在此缓存内。
    """
    key = _embedding_cache_key(text)
    vector = _read_embedding_cache(key)
    if vector is None:
        vector = get_embedder().embed_query(text)
        _write_embedding_cache(key, vector)
    return tuple(vector)


def embed_texts_cached(texts: list[str]) -> list[list[float]]:
    """批量文本向量：优先 Redis 命中，未命中才调用 embedder 并写回。

    与 embed_query_cached 不同，这里不做进程内缓存——文档块数量大，
    驻留内存的收益低；Redis 命中已能覆盖跨 worker 的重索引/对账场景。
    """
    if not texts:
        return []
    if not settings.embedding_cache_enabled:
        return get_embedder().embed_texts(texts)

    keys = [_embedding_cache_key(text) for text in texts]
    results: list[list[float] | None] = [None] * len(texts)
    client = get_redis()
    if client is not None:
        try:
            raws = client.mget(keys)
            for i, raw in enumerate(raws):
                if raw is not None:
                    try:
                        results[i] = json.loads(raw)
                    except (ValueError, TypeError):
                        results[i] = None
        except Exception:  # noqa: BLE001
            pass

    missing = [i for i, value in enumerate(results) if value is None]
    if missing:
        vectors = get_embedder().embed_texts([texts[i] for i in missing])
        if len(vectors) != len(missing):
            raise RuntimeError("embedder 返回向量数与输入数不一致")
        if client is not None:
            try:
                pipe = client.pipeline()
                for offset, index in enumerate(missing):
                    pipe.set(
                        keys[index],
                        _encode_vector(vectors[offset]),
                        ex=int(settings.embedding_cache_ttl_seconds),
                    )
                pipe.execute()
            except Exception:  # noqa: BLE001
                pass  # 写失败只损失缓存收益
        for offset, index in enumerate(missing):
            results[index] = vectors[offset]

    return [result for result in results if result is not None]
