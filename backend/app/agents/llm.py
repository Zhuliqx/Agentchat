"""LLM 工厂：根据配置创建 LangChain ChatModel。

支持 provider:
- ollama      : 本地 Ollama（默认 qwen2.5:7b）
- openai      : OpenAI 或任何 OpenAI 兼容 API
- deepseek    : DeepSeek 官方 API（OpenAI 兼容）
- dashscope   : 阿里通义千问（OpenAI 兼容）

运行时模型切换：`available_models()` 列出可用模型，`set_current_model()` 持久化
选择并清空 LLM/图缓存，此后 `get_llm()` 按所选模型创建（重启后仍生效）。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

# 运行时模型选择持久化文件（data/ 已 gitignore）
MODEL_CHOICE_FILE = BASE_DIR / "data" / "model_choice.json"

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


def _openai_kwargs() -> dict:
    """OpenAI 兼容系共享的超时/重试参数（网络抖动防挂起、防偶发失败中断对话）。"""
    return {
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
    }


def _model_name(kind: str) -> str:
    """当前 provider 下的模型名；kind="light" 且配置了 LLM_LIGHT_MODEL 时用轻量模型。"""
    if kind == "light" and settings.llm_light_model:
        return settings.llm_light_model
    p = settings.llm_provider.lower()
    if p == "deepseek":
        return settings.deepseek_model
    if p == "dashscope":
        return settings.dashscope_model
    return settings.llm_model or settings.openai_model


def create_llm(provider: str, model: str) -> "BaseChatModel":
    """按指定 provider + 模型名创建 ChatModel（不缓存）。

    供运行时模型切换使用；provider 取 settings 支持的任意值。
    """
    p = provider.lower()
    if p == "ollama":
        from langchain_ollama import ChatOllama

        # 本地 Ollama：无需网络超时/重试
        return ChatOllama(
            model=model,
            temperature=settings.temperature,
            base_url="http://localhost:11434",
        )

    if p == "deepseek":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=settings.temperature,
            **_openai_kwargs(),
        )

    if p == "dashscope":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            temperature=settings.temperature,
            **_openai_kwargs(),
        )

    # 默认 openai（兼容任意 OpenAI 兼容端点）
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=settings.temperature,
        **_openai_kwargs(),
    )


@lru_cache
def get_llm(kind: str = "main") -> "BaseChatModel":
    """获取 ChatModel。

    - kind="main"：Supervisor 使用（主模型）。
    - kind="light"：子 Agent 使用；配置了 LLM_LIGHT_MODEL 时用轻量模型，
      未配置则回退到与 main 相同（行为不变）。

    运行时切换了模型（`set_current_model`）时，Supervisor 用所选模型；
    子 Agent（kind="light"）在配置了 LLM_LIGHT_MODEL 时仍用轻量模型。
    """
    choice = get_current_model_choice()
    if choice is not None:
        if kind == "light" and settings.llm_light_model:
            return create_llm(settings.llm_provider, settings.llm_light_model)
        return create_llm(choice["provider"], choice["model"])
    if kind == "light" and not settings.llm_light_model:
        return get_llm("main")
    return create_llm(settings.llm_provider, _model_name(kind))


# ---------------- 运行时模型切换 ----------------

def get_current_model_choice() -> dict | None:
    """读取持久化的模型选择 {"provider": ..., "model": ...}，无则返回 None。"""
    try:
        if MODEL_CHOICE_FILE.exists():
            data = json.loads(MODEL_CHOICE_FILE.read_text(encoding="utf-8"))
            if data.get("provider") and data.get("model"):
                return {"provider": data["provider"], "model": data["model"]}
    except Exception:
        pass
    return None


def available_models() -> list[dict]:
    """可用模型列表（基于 .env 已配置的 API key），供前端下拉选择。

    每项：{id, provider, model, label}。
    """
    models: list[dict] = []
    if settings.deepseek_api_key:
        models.append(
            {
                "id": "deepseek:deepseek-chat",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "label": "DeepSeek Chat（deepseek-chat）",
            }
        )
        models.append(
            {
                "id": "deepseek:deepseek-reasoner",
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "label": "DeepSeek Reasoner（深度推理）",
            }
        )
    if settings.dashscope_api_key:
        for m, d in [("qwen-plus", "通用"), ("qwen-max", "更强推理"), ("qwen-turbo", "更快")]:
            models.append(
                {
                    "id": f"dashscope:{m}",
                    "provider": "dashscope",
                    "model": m,
                    "label": f"通义 {m}（{d}）",
                }
            )
    if settings.openai_api_key:
        models.append(
            {
                "id": f"openai:{settings.openai_model}",
                "provider": "openai",
                "model": settings.openai_model,
                "label": f"OpenAI {settings.openai_model}",
            }
        )
    if settings.llm_provider.lower() == "ollama":
        models.append(
            {
                "id": f"ollama:{settings.llm_model}",
                "provider": "ollama",
                "model": settings.llm_model,
                "label": f"Ollama {settings.llm_model}",
            }
        )
    if not models:
        # 兜底：至少返回当前默认
        models.append(
            {
                "id": f"{settings.llm_provider}:{_model_name('main')}",
                "provider": settings.llm_provider,
                "model": _model_name("main"),
                "label": f"当前默认（{_model_name('main')}）",
            }
        )
    return models


def set_current_model(model_id: str) -> bool:
    """切换当前模型并清空 LLM/图缓存，使后续请求用新模型。返回是否成功。"""
    for m in available_models():
        if m["id"] == model_id:
            try:
                MODEL_CHOICE_FILE.parent.mkdir(parents=True, exist_ok=True)
                MODEL_CHOICE_FILE.write_text(
                    json.dumps({"provider": m["provider"], "model": m["model"]}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("模型选择持久化失败: %s", exc)
            get_llm.cache_clear()
            try:
                from app.agents import graph

                graph.clear_graph_cache()
            except Exception:
                pass  # 图缓存清除失败不阻塞（下次请求仍按新模型构建）
            logger.info("模型已切换: %s", model_id)
            return True
    return False
