"""模型切换接口：查询可用模型 / 切换当前模型。

运行时切换模型（持久化到 backend/data/model_choice.json），切换后清空
LLM/图缓存，后续对话请求使用新模型。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.llm import (
    available_models,
    get_current_model_choice,
    set_current_model,
)

router = APIRouter()


class ModelChoiceIn(BaseModel):
    model_id: str


@router.get("")
def list_models() -> dict:
    """可用模型列表 + 当前选择。"""
    return {"models": available_models(), "current": get_current_model_choice()}


@router.put("/current")
def set_model(body: ModelChoiceIn) -> dict:
    """切换当前模型（清缓存，立即生效）。"""
    if not set_current_model(body.model_id):
        raise HTTPException(400, f"未知的模型: {body.model_id}")
    return {"models": available_models(), "current": get_current_model_choice()}
