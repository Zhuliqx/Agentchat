"""运行时配置：把部分 Settings 参数持久化到数据库（app_settings 表）。

- 启动时 load_runtime_settings() 从 DB 读取并覆盖 settings 对象（优先级高于 .env）；
- 运行时可 get/save，便于管理后台在线调整，无需改文件/重启。

仅开放影响小、可安全热改的参数（检索/生成相关）；改动 chunk 大小、模型等
需要重建索引/重摄入的参数不在此列。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

# 可配置项：key(小写) -> (类型, 中文说明)
CONFIG_ITEMS: dict[str, tuple[type, str]] = {
    "rag_top_k": (int, "知识库检索 Top-K"),
    "rag_score_threshold": (float, "检索分数阈值"),
    "rag_max_per_doc": (int, "同文档最多保留块数"),
    "hybrid_search": (bool, "混合检索（向量+BM25）"),
    "rerank_enabled": (bool, "Rerank 精排"),
    "temperature": (float, "LLM 温度"),
}


def _cast(v: Any, typ: type):
    if typ is bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    try:
        return typ(v)
    except (TypeError, ValueError):
        return None


def _ensure_table() -> None:
    from app.db.postgres import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app_settings "
                "(key VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)"
            )
        )


def load_runtime_settings() -> None:
    """启动时从 DB 读取运行时配置并覆盖 settings（幂等、容错）。"""
    try:
        _ensure_table()
        from app.db.postgres import engine

        with engine.begin() as conn:
            rows = conn.execute(text("SELECT key, value FROM app_settings")).fetchall()
        for key, value in rows:
            item = CONFIG_ITEMS.get(key)
            if item is None:
                continue
            typ, _label = item
            try:
                casted = _cast(value, typ)
                if casted is not None:
                    setattr(settings, key, casted)
            except Exception:
                pass
    except Exception as exc:  # 配置表不可用时不阻塞启动
        logger.warning("运行时配置加载失败（忽略）: %s", exc)


def get_runtime_settings() -> list[dict]:
    """返回可配置项当前值（含类型与说明）。"""
    return [
        {
            "key": k,
            "type": t.__name__,
            "label": label,
            "value": getattr(settings, k),
        }
        for k, (t, label) in CONFIG_ITEMS.items()
    ]


def save_runtime_settings(values: dict[str, Any]) -> list[dict]:
    """保存配置到 DB 并立即覆盖 settings；返回更新后的配置列表。"""
    _ensure_table()
    from app.db.postgres import engine

    with engine.begin() as conn:
        for key, raw in values.items():
            item = CONFIG_ITEMS.get(key)
            if item is None:
                continue
            typ, _label = item
            casted = _cast(str(raw), typ)
            if casted is None:
                continue
            setattr(settings, key, casted)
            conn.execute(
                text(
                    "INSERT INTO app_settings(key, value) VALUES(:k, :v) "
                    "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value"
                ),
                {"k": key, "v": str(casted)},
            )
    return get_runtime_settings()
