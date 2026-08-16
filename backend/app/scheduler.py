"""后台任务调度器：扫描 tasks 表执行到期的定时/批处理任务。

设计：
- 无第三方依赖（不引入 APScheduler），用 asyncio 后台循环实现；
- 每 15 秒扫描一次 tasks 表，对 enabled 且 next_run_at 到期的任务执行；
- 任务体是同步函数，统一放线程池（asyncio.to_thread）执行，避免阻塞事件循环；
- 任务执行结果（状态/错误/下次运行时间）写回 tasks 表。

调度表达式（schedule 字段）：
- "interval:<秒>"  固定间隔，如 interval:3600 每小时一次
- "cron:<分钟>"     分钟级 cron，如 cron:*/30 每小时第 30 分钟、cron:0 每小时整点
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.db import postgres

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SEC = 15


# ---------------- 任务注册表 ----------------

def _run_reindex_documents() -> dict:
    """全量重建知识库索引：按 (用户, source) 逐文件重新摄入。"""
    from sqlalchemy import select

    from app.db.models import Document
    from app.db.postgres import SessionLocal
    from app.rag.ingestion import ingest_file
    from pathlib import Path

    with SessionLocal() as db:
        rows = db.execute(
            select(Document.user_id, Document.source).distinct()
        ).all()
    pairs = [(u, s) for u, s in rows if s]

    results = {"sources": len(pairs), "changed": 0, "errors": 0}
    for user_id, source in pairs:
        try:
            r = ingest_file(Path(source), user_id=user_id)
            if not r.get("unchanged"):
                results["changed"] += 1
        except Exception as exc:
            results["errors"] += 1
            logger.warning("reindex 失败 %s@%s: %s", user_id, source, exc)
    return results


def _run_cleanup_checkpoints() -> dict:
    """清理孤儿 checkpoint（对应会话已删除的）。"""
    from app.db.memory_store import cleanup_stale_checkpoints

    n = cleanup_stale_checkpoints()
    return {"cleaned": n}


def _run_vacuum_documents() -> dict:
    """清理源文件已不存在的文档记录（Postgres 行 + Milvus 向量，按用户分组）。"""
    from pathlib import Path

    from sqlalchemy import select

    from app.db.models import Document
    from app.db.postgres import SessionLocal
    from app.rag import vector_store

    with SessionLocal() as db:
        rows = db.execute(
            select(Document.user_id, Document.source).distinct()
        ).all()
    pairs = [(u, s) for u, s in rows if s]

    removed_ids: list[str] = []
    removed_sources: list[str] = []
    for user_id, source in pairs:
        if Path(source).exists():
            continue
        with SessionLocal() as db:
            ids = [
                r[0]
                for r in db.query(Document.id)
                .filter(Document.source == source, Document.user_id == user_id)
                .all()
            ]
            if ids:
                db.query(Document).filter(
                    Document.source == source, Document.user_id == user_id
                ).delete(synchronize_session=False)
                db.commit()
        if ids:
            removed_ids.extend(ids)
            removed_sources.append(f"{user_id}@{source}")
    if removed_ids:
        try:
            vector_store.delete_by_ids(removed_ids)
        except Exception as exc:
            logger.warning("vacuum 删除 Milvus 向量失败: %s", exc)
    return {"removed_sources": len(removed_sources), "removed_chunks": len(removed_ids)}


TASK_REGISTRY: dict[str, dict] = {
    "reindex_documents": {
        "label": "重建知识库索引",
        "desc": "读取 Postgres 中全部文档 source，逐文件重新摄入（增量去重）",
        "fn": _run_reindex_documents,
    },
    "cleanup_checkpoints": {
        "label": "清理孤儿 Checkpoint",
        "desc": "删除已删除会话对应的 LangGraph checkpoint，防止 DB 膨胀",
        "fn": _run_cleanup_checkpoints,
    },
    "vacuum_documents": {
        "label": "清理失效文档",
        "desc": "删除源文件已不存在的文档记录（Postgres 行 + Milvus 向量）",
        "fn": _run_vacuum_documents,
    },
}


# ---------------- 调度计算 ----------------

def compute_next_run(schedule: str, now: datetime | None = None) -> datetime | None:
    """根据调度表达式计算下次运行时间；非法表达式返回 None（视为不调度）。"""
    now = now or datetime.now(timezone.utc)
    if schedule.startswith("interval:"):
        try:
            sec = int(schedule.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
        if sec <= 0:
            return None
        return now + timedelta(seconds=sec)

    if schedule.startswith("cron:"):
        expr = schedule.split(":", 1)[1].strip()
        try:
            if expr == "*" or expr == "*/1":
                return now + timedelta(minutes=1)
            if expr.startswith("*/"):
                step = int(expr[2:])
                if step <= 0:
                    return None
                # 对齐到下一个 step 分钟点
                minute = now.minute
                next_min = (minute // step + 1) * step
                if next_min >= 60:
                    nxt = (now.replace(second=0, microsecond=0) + timedelta(hours=1)).replace(
                        minute=0
                    )
                else:
                    nxt = now.replace(minute=next_min, second=0, microsecond=0)
                return nxt
            m = int(expr)
            nxt = now.replace(second=0, microsecond=0)
            if nxt.minute >= m:
                nxt = nxt + timedelta(hours=1)
            return nxt.replace(minute=m)
        except (ValueError, IndexError):
            return None
    return None


# ---------------- 调度循环 ----------------

async def _run_task(task_id: str) -> None:
    """执行单个任务（线程池），并回写结果。"""
    task = postgres.get_task(task_id)
    if task is None:
        return
    entry = TASK_REGISTRY.get(task.task_type)
    if entry is None:
        postgres.mark_task_result(task_id, "failed", "未知任务类型", None)
        return

    logger.info("执行任务 %s (%s)", task.name, task.task_type)
    try:
        result = await asyncio.to_thread(entry["fn"])
        next_run = compute_next_run(task.schedule)
        postgres.mark_task_result(task_id, "success", None, next_run)
        logger.info("任务完成 %s: %s", task.name, result)
    except Exception as exc:
        logger.exception("任务失败 %s", task.name)
        next_run = compute_next_run(task.schedule)
        postgres.mark_task_result(task_id, "failed", str(exc)[:500], next_run)


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    """调度主循环：扫描 tasks 表，执行到期的任务。"""
    logger.info("任务调度器已启动（扫描间隔 %ss）", _SCAN_INTERVAL_SEC)
    # 首次启动：为无 next_run_at 的已启用任务补算，并把长期卡死的任务标记失败
    for t in postgres.list_tasks():
        if t.enabled and t.next_run_at is None:
            nxt = compute_next_run(t.schedule)
            postgres.mark_task_result(t.id, t.last_status or "", None, nxt)
        if t.enabled and t.last_status == "running":
            postgres.mark_task_result(t.id, "failed", "上次运行异常中断（超时/重启）", None)

    while not stop_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            for t in postgres.list_tasks():
                if not t.enabled or t.next_run_at is None:
                    continue
                if t.next_run_at.tzinfo is None:
                    t_next = t.next_run_at.replace(tzinfo=timezone.utc)
                else:
                    t_next = t.next_run_at
                if t_next <= now:
                    asyncio.create_task(_run_task(t.id))
            await asyncio.wait_for(stop_event.wait(), timeout=_SCAN_INTERVAL_SEC)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            logger.warning("调度循环异常: %s", exc)
            await asyncio.sleep(_SCAN_INTERVAL_SEC)
