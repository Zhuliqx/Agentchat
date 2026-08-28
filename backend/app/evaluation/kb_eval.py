"""知识库在线检索评估（管理后台用）：自动/内置/自定义案例 + 后端统一执行。

- 自动案例：从每个文档的块中提取特征词，验证该文档能否被检索到；
- 内置案例：检索链路连通性检查（不依赖特定知识库内容，判定=返回结果数>0）；
- 自定义案例：管理员在管理后台维护的 {query, keywords} 列表。

判定口径与 ``scripts/eval_rag.py`` 一致（来源命中/关键词命中），避免路由层
与评估脚本各写一份判定逻辑。
"""
from __future__ import annotations

import json
import re

from sqlalchemy import text

from app.config import settings
from app.db.models import Document
from app.db.postgres import SessionLocal, engine
from app.db.runtime_settings import _ensure_table
from app.rag import vector_store
from app.rag.retriever import get_retriever


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


def run_kb_eval(
    user_id: str,
    include_auto: bool = True,
    include_builtin: bool = True,
    custom_only: bool = False,
) -> dict:
    """后端统一执行评估：对每个案例走完整检索链路（混合检索+rerank+去重）。

    按案例类型判定命中：auto=该文档能否被召回；builtin=是否有结果；
    custom=检索文本是否含关键词。
    """
    cases: list[dict] = []
    if not custom_only:
        if include_auto:
            cases += _auto_cases(user_id)
        if include_builtin:
            cases += _BUILTIN_CASES
    cases += _load_custom_cases()
    if not cases:
        return {"results": [], "hit": 0, "total": 0, "hit_rate": None}

    retriever = get_retriever(user_id=user_id)
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
