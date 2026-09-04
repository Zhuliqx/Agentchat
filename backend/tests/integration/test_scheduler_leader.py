"""调度器 advisory lock 选主集成测试（需 Postgres，不影响真实调度器）。"""
from __future__ import annotations

import asyncio
import random
import time
import uuid

import pytest

from helpers import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(), reason="需要运行中的 Postgres"
)


def test_scheduler_leader_executes_task_once(monkeypatch):
    """两个并发调度循环只允许 leader 扫描，到期任务只执行一次。"""
    import app.scheduler as scheduler_mod
    from app.db import postgres
    from app.db.models import Task
    from app.db.postgres import SessionLocal
    from app.scheduler import scheduler_loop

    suffix = uuid.uuid4().hex[:6]
    task = postgres.create_task(
        f"leader-test-{suffix}",
        "cleanup_checkpoints",
        "interval:3600",
    )
    with SessionLocal() as db:
        t = db.get(Task, task.id)
        t.next_run_at = postgres.utcnow()
        t.enabled = True
        db.commit()

    real_run = scheduler_mod._run_task
    run_calls: list[str] = []

    async def tracked_run(task_id: str):
        run_calls.append(task_id)
        return await real_run(task_id)

    monkeypatch.setattr(scheduler_mod, "_run_task", tracked_run)

    async def scenario() -> None:
        stop = asyncio.Event()
        lock_key = 700000 + random.randint(0, 99999)
        loops = [
            asyncio.create_task(scheduler_loop(stop, advisory_lock_key=lock_key)),
            asyncio.create_task(scheduler_loop(stop, advisory_lock_key=lock_key)),
        ]
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = await asyncio.to_thread(postgres.get_task, task.id)
                if current and current.last_status == "success":
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("任务未在限时内由 leader 执行")
        finally:
            stop.set()
            await asyncio.gather(*loops, return_exceptions=True)

    try:
        asyncio.run(scenario())
        final = postgres.get_task(task.id)
        assert final is not None and final.last_status == "success"
        assert len(run_calls) == 1, f"任务被执行了 {len(run_calls)} 次"
    finally:
        postgres.delete_task(task.id)
