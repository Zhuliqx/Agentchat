"""管理员后台接口（需 require_admin：用户名在 ADMIN_USERNAMES 中）。

提供平台统计、用户列表与用户删除；删除用户复用 auth.purge_user_data
（向量/文档/记忆/checkpoint/用户级联清理）。
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text

from app.api.deps import is_admin_username, require_admin
from app.config import settings
from app.db import postgres
from app.db.models import Document, Message, Session, User
from app.db.postgres import SessionLocal, engine
from app.db.runtime_settings import (
    _ensure_table,
    get_runtime_settings,
    save_runtime_settings,
)
from app.rag import vector_store
from app.rag.retriever import get_retriever
from app.api.routes.auth import purge_user_data

router = APIRouter()


@router.get("/stats")
def admin_stats(_admin_id: str = Depends(require_admin)) -> dict:
    """平台整体统计（用户/会话/消息/文档）。"""
    with SessionLocal() as db:
        return {
            "user_count": db.query(func.count(User.id)).scalar() or 0,
            "session_count": db.query(func.count(Session.id)).scalar() or 0,
            "message_count": db.query(func.count(Message.id)).scalar() or 0,
            "document_count": db.query(func.count(Document.id)).scalar() or 0,
        }


@router.get("/usage")
def admin_usage(_admin_id: str = Depends(require_admin)) -> dict:
    """按天聚合消息量（最近 14 天），并估算 token 消耗。"""
    with SessionLocal() as db:
        day = func.date(func.timezone("UTC", Message.created_at))
        rows = (
            db.query(day, func.count(Message.id)).group_by(day).order_by(day).all()
        )
    items = [
        {"date": str(d), "messages": int(c), "tokens": int(c) * 200}
        for d, c in rows
    ]
    return {
        "items": items[-14:],
        "total_messages": sum(x["messages"] for x in items),
        "total_tokens": sum(x["tokens"] for x in items),
    }


class SettingsIn(BaseModel):
    """保存系统设置请求体（key -> 原始值字符串/数字）。"""

    values: dict[str, str | int | float | bool]


@router.get("/settings")
def admin_get_settings(_admin_id: str = Depends(require_admin)) -> dict:
    """返回可在线调整的系统设置（检索/生成相关）。"""
    return {"items": get_runtime_settings()}


@router.put("/settings")
def admin_save_settings(
    body: SettingsIn, _admin_id: str = Depends(require_admin)
) -> dict:
    """保存系统设置到 DB 并立即生效。"""
    return {"items": save_runtime_settings(body.values)}


@router.get("/users")
def admin_users(_admin_id: str = Depends(require_admin)) -> list[dict]:
    """全部用户列表（含各用户会话/消息/文档统计与管理员标记）。"""
    with SessionLocal() as db:
        users = db.query(User).order_by(User.created_at.asc()).all()
        out = []
        for u in users:
            session_count = (
                db.query(func.count(Session.id))
                .filter(Session.user_id == u.id)
                .scalar()
                or 0
            )
            message_count = (
                db.query(func.count(Message.id))
                .join(Session, Message.session_id == Session.id)
                .filter(Session.user_id == u.id)
                .scalar()
                or 0
            )
            document_count = (
                db.query(func.count(Document.id))
                .filter(Document.user_id == u.id)
                .scalar()
                or 0
            )
            out.append(
                {
                    "id": u.id,
                    "username": u.username,
                    "avatar_color": u.avatar_color,
                    "created_at": u.created_at.isoformat(),
                    "is_admin": is_admin_username(u.username),
                    "session_count": session_count,
                    "message_count": message_count,
                    "document_count": document_count,
                }
            )
        return out


@router.delete("/users/{user_id}", status_code=204)
async def admin_delete_user(
    user_id: str, admin_id: str = Depends(require_admin)
):
    """管理员删除指定用户（不可删除访客或自己）。"""
    if user_id == admin_id:
        raise HTTPException(400, "不能删除自己的账号")
    if user_id == settings.guest_user_id:
        raise HTTPException(400, "不能删除访客账号")
    if not postgres.get_user(user_id):
        raise HTTPException(404, "用户不存在")
    await purge_user_data(user_id)


# ---------------- 知识库检索质量评估（后端统一执行） ----------------


def _basename(p: str) -> str:
    return (p or "").replace("\\", "/").rsplit("/", 1)[-1]


def _load_custom_cases() -> list[dict]:
    """读取用户自定义评估案例（app_settings.eval_custom_cases，JSON 数组）。"""
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT value FROM app_settings WHERE key = 'eval_custom_cases'")
            ).fetchone()
        if not row:
            return []
        data = json.loads(row[0])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_custom_cases(cases: list[dict]) -> None:
    """保存自定义评估案例（key -> JSON 数组，幂等 upsert）。"""
    _ensure_table()
    payload = [
        {
            "query": str(c.get("query", "")).strip(),
            "keywords": [
                str(k).strip()
                for k in (c.get("keywords") or [])
                if str(k).strip()
            ],
        }
        for c in cases
        if str(c.get("query", "")).strip()
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO app_settings(key, value) VALUES ('eval_custom_cases', :v) "
                "ON CONFLICT(key) DO UPDATE SET value = :v"
            ),
            {"v": json.dumps(payload, ensure_ascii=False)},
        )


def _doc_sources(user_id: str) -> list[str]:
    """当前用户知识库的文档 source 列表（去重）。"""
    with SessionLocal() as db:
        rows = (
            db.query(Document.source)
            .filter(Document.user_id == user_id)
            .distinct()
            .all()
        )
        return [r[0] for r in rows]


def _escape_milvus(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _chunk_texts(source: str, limit: int = 4) -> list[str]:
    """取某个文档（source）的前若干块文本（Milvus 按 source 过滤）。"""
    try:
        client = vector_store._client()
        expr = f'source == "{_escape_milvus(source)}"'
        res = client.query(
            settings.milvus_collection,
            filter=expr,
            output_fields=["text"],
            limit=limit,
        )
        return [r.get("text", "") for r in res]
    except Exception:
        return []


_KEYWORD_RE = re.compile(r"[\u4e00-\u9fff]{2,10}")


def _extract_keywords(text: str, n: int = 3) -> list[str]:
    """从文本提取候选关键词：连续中文片段，按长度排序取较长的（更有区分度）。"""
    seen: list[str] = []
    for w in _KEYWORD_RE.findall(text):
        if len(w) >= 2 and w not in seen:
            seen.append(w)
    seen.sort(key=len, reverse=True)
    return seen[:n]


def _auto_cases(user_id: str, per_doc: int = 2) -> list[dict]:
    """自动适配案例：从每个文档的块中提取特征词，验证该文档能否被检索到。

    判定依据是「检索结果是否包含该文档（source 匹配）」，而不是关键词——
    因为特征词只是线索，真正要验证的是每个文档都能被检索召回。
    """
    cases: list[dict] = []
    for source in _doc_sources(user_id):
        texts = _chunk_texts(source, limit=per_doc * 2)
        pool: list[str] = []
        for t in texts:
            pool.extend(_extract_keywords(t, n=2))
        uniq: list[str] = []
        for k in pool:
            if k not in uniq:
                uniq.append(k)
            if len(uniq) >= per_doc:
                break
        for kw in uniq[:per_doc]:
            cases.append(
                {
                    "query": kw,
                    "keywords": [kw],
                    "source": source,
                    "doc": _basename(source),
                    "type": "auto",
                }
            )
    return cases


# 内置案例：检索连通性检查（不依赖特定知识库内容，判定=返回结果数>0）
_BUILTIN_CASES: list[dict] = [
    {
        "query": "检索测试",
        "keywords": [],
        "check": "any",
        "type": "builtin",
        "label": "检索链路连通性",
    },
    {
        "query": "内容概述",
        "keywords": [],
        "check": "any",
        "type": "builtin",
        "label": "常规查询可用性",
    },
]


class EvalRunIn(BaseModel):
    """运行评估请求：选择包含的案例来源。"""

    include_auto: bool = True
    include_builtin: bool = True
    custom_only: bool = False


@router.get("/eval")
def admin_eval(admin_id: str = Depends(require_admin)) -> dict:
    """返回评估配置：当前知识库文档、内置/自动/自定义案例。"""
    docs = [
        {"source": s, "name": _basename(s)} for s in _doc_sources(admin_id)
    ]
    return {
        "docs": docs,
        "builtin": _BUILTIN_CASES,
        "auto": _auto_cases(admin_id),
        "custom": _load_custom_cases(),
    }


class EvalCasesIn(BaseModel):
    """保存自定义案例请求体。"""

    cases: list[dict] = []


@router.put("/eval/custom")
def admin_save_eval_cases(
    body: EvalCasesIn, _admin_id: str = Depends(require_admin)
) -> dict:
    """保存自定义评估案例（持久化到 app_settings）。"""
    _save_custom_cases(body.cases)
    return {"custom": _load_custom_cases()}


@router.post("/eval/run")
def admin_run_eval(
    body: EvalRunIn, admin_id: str = Depends(require_admin)
) -> dict:
    """后端统一执行评估：对每个案例走完整检索链路（混合检索+rerank+去重），

    按案例类型判定命中：auto=该文档能否被召回；builtin=是否有结果；
    custom=检索文本是否含关键词。
    """
    cases: list[dict] = []
    if not body.custom_only:
        if body.include_auto:
            cases += _auto_cases(admin_id)
        if body.include_builtin:
            cases += _BUILTIN_CASES
    cases += _load_custom_cases()
    if not cases:
        return {"results": [], "hit": 0, "total": 0, "hit_rate": None}

    retriever = get_retriever(user_id=admin_id)
    results: list[dict] = []
    for c in cases:
        try:
            docs = retriever.invoke(c["query"])
            hits = [
                {"text": d.page_content, "source": d.metadata.get("source", "")}
                for d in docs
            ]
            if c.get("source"):
                # 自动适配案例：验证该文档能否被检索到
                hit = any(h["source"] == c["source"] for h in hits)
            elif c.get("check") == "any":
                # 内置连通性案例：只要返回结果即健康
                hit = len(hits) > 0
            else:
                # 自定义案例：检索文本/来源含任一关键词
                joined = " ".join(
                    f"{h['text']} {h['source']}" for h in hits
                ).lower()
                hit = any(
                    str(k).lower() in joined for k in c.get("keywords", [])
                )
            results.append(
                {
                    "query": c["query"],
                    "keywords": c.get("keywords", []),
                    "type": c.get("type", "custom"),
                    "doc": c.get("doc"),
                    "hit": hit,
                    "hits": len(hits),
                    "first": (
                        hits[0]["text"].replace("\n", " ")[:60] if hits else ""
                    ),
                }
            )
        except Exception:
            results.append(
                {
                    "query": c["query"],
                    "keywords": c.get("keywords", []),
                    "type": c.get("type", "custom"),
                    "doc": c.get("doc"),
                    "hit": False,
                    "hits": 0,
                    "first": "检索失败",
                }
            )
    hit_n = sum(1 for r in results if r["hit"])
    return {
        "results": results,
        "hit": hit_n,
        "total": len(results),
        "hit_rate": round(hit_n / len(results) * 100) if results else None,
    }
