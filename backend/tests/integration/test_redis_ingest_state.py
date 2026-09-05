"""摄入任务状态 Redis 集成测试（需运行中的 Redis）。

Redis 启用后，摄入任务快照必须能在本进程内存被清空（等价于另一个 worker
只持有 Redis）时仍被读取；这是跨 worker 查询进度的核心契约。
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException

from helpers import redis_available

pytestmark = pytest.mark.skipif(
    not redis_available(), reason="需要运行中的 Redis"
)


def _enable_redis(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "redis_enabled", True)


def _clean(rag_mod, client, key: str, task_id: str) -> None:
    client.delete(key)
    with rag_mod._INGEST_LOCK:
        rag_mod._INGEST_TASKS.pop(task_id, None)


def test_ingest_task_state_readable_without_local_memory(monkeypatch):
    """状态写入 Redis 后，即使本进程内存被清空也能按任务 id 读回。"""
    from app.api.routes import rag as rag_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    user_id = f"ingest-user-{uuid.uuid4().hex[:8]}"
    task_id = uuid.uuid4().hex
    key = redis_key("ingest", "task", task_id)
    client = get_redis()
    assert client is not None
    client.delete(key)
    try:
        rag_mod._create_ingest_task(
            task_id,
            {
                "status": "pending",
                "progress": 0,
                "stage": "排队中",
                "filename": "demo.txt",
                "user_id": user_id,
            },
        )
        rag_mod._update_ingest_task(
            task_id,
            status="processing",
            progress=50,
            stage="嵌入中",
        )

        # 模拟另一个 worker：本进程没有该任务的内存记录，只能从 Redis 读
        with rag_mod._INGEST_LOCK:
            rag_mod._INGEST_TASKS.pop(task_id, None)

        body = rag_mod.ingest_status(task_id, user_id)
        assert body["status"] == "processing"
        assert body["progress"] == 50
        assert body["stage"] == "嵌入中"
        assert body["filename"] == "demo.txt"

        # Redis 中的快照是完整 JSON，可供任意 worker 反序列化
        raw = client.get(key)
        assert raw is not None
        assert json.loads(raw)["status"] == "processing"

        # 越权校验不因状态迁到 Redis 而失效
        with pytest.raises(HTTPException) as exc:
            rag_mod.ingest_status(task_id, "other-user")
        assert exc.value.status_code == 404
    finally:
        _clean(rag_mod, client, key, task_id)


def test_ingest_task_key_has_retention_ttl(monkeypatch):
    """摄入任务 key 必须有保留 TTL，避免 Redis 中堆积过期任务。"""
    from app.api.routes import rag as rag_mod
    from app.cache.redis_client import get_redis, redis_key

    _enable_redis(monkeypatch)
    task_id = uuid.uuid4().hex
    key = redis_key("ingest", "task", task_id)
    client = get_redis()
    assert client is not None
    client.delete(key)
    try:
        rag_mod._create_ingest_task(task_id, {"status": "pending", "user_id": "u"})
        ttl = client.ttl(key)
        assert 0 < ttl <= rag_mod._INGEST_TASK_TTL_SEC
    finally:
        _clean(rag_mod, client, key, task_id)
