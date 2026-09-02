"""长期记忆管理接口（基于 LangGraph Store / PostgresStore）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id
from app.db.memory_store import get_store, safe_asearch, store_has_index

router = APIRouter()

NAMESPACE_TAIL = "memories"


class MemoryOut(BaseModel):
    id: str
    user_id: str
    content: str
    created_at: str
    updated_at: str


class MemoryIn(BaseModel):
    user_id: str = "default"  # 兼容旧客户端；实际归属以认证用户为准
    content: str = Field(..., min_length=1, max_length=2000)


def _out(user_id: str, item) -> MemoryOut:
    return MemoryOut(
        id=item.key,
        user_id=user_id,
        content=item.value.get("content", ""),
        created_at=item.created_at.isoformat() if item.created_at else "",
        updated_at=item.updated_at.isoformat() if item.updated_at else "",
    )


def _require_store():
    store = get_store()
    if store is None:
        raise HTTPException(503, "长期记忆存储未就绪")
    return store


@router.get("", response_model=list[MemoryOut])
async def list_memory(
    user_id: str = Depends(get_current_user_id), query: str = ""
):
    """查看某用户的长期记忆（从 LangGraph Store 读取）。

    - query 为空：按更新时间倒序返回全部。
    - query 非空且 Store 启用了语义索引：按语义相似度返回 Top-K。
    - Store 无索引（pgvector 未启用）：忽略 query，返回全部。
    """
    store = _require_store()
    namespace = (user_id, NAMESPACE_TAIL)
    items = None
    if query.strip() and store_has_index():
        # 语义检索失败（无索引等）→ None → 降级全量
        items = await safe_asearch(store, namespace, query=query.strip(), limit=100)
    if items is None:
        items = await store.asearch(namespace, limit=100)  # 兜底失败照常上抛
    return [_out(user_id, i) for i in items]


@router.post("", response_model=MemoryOut, status_code=201)
async def add_memory(body: MemoryIn, user_id: str = Depends(get_current_user_id)):
    """手动保存一条长期记忆到 Store（归属当前认证用户）。"""
    store = _require_store()
    key = uuid.uuid4().hex
    await store.aput((user_id, NAMESPACE_TAIL), key, {"content": body.content})
    return MemoryOut(
        id=key, user_id=user_id,
        content=body.content, created_at="", updated_at="",
    )


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, user_id: str = Depends(get_current_user_id)):
    """删除一条记忆。"""
    store = _require_store()
    item = await store.aget((user_id, NAMESPACE_TAIL), memory_id)
    if item is None:
        raise HTTPException(404, "记忆不存在")
    await store.adelete((user_id, NAMESPACE_TAIL), memory_id)


@router.delete("", status_code=204)
async def clear_memory(user_id: str = Depends(get_current_user_id)):
    """清空某用户的全部记忆。"""
    store = _require_store()
    items = await store.asearch((user_id, NAMESPACE_TAIL), limit=1000)
    for i in items:
        await store.adelete((user_id, NAMESPACE_TAIL), i.key)
