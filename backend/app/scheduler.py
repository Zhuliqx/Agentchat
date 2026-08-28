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
    from pathlib import Path

    from app.db.postgres import distinct_document_sources
    from app.rag.ingestion import ingest_file

    pairs = distinct_document_sources()

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

    from app.db.models import Document
    from app.db.postgres import SessionLocal, distinct_document_sources
    from app.rag import vector_store

    pairs = distinct_document_sources()

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


def _sync_pending_vectors(limit: int = 200) -> tuple[int, int]:
    """把 vector_status='pending' 的行嵌入并同步到 Milvus，标记 synced。

    Postgres 为事实源：摄入时 Milvus 同步失败的行保持 pending，由本步骤重试
    （幂等 sync_chunks，不产生重复向量）。返回 (成功行数, 失败组数)。
    """
    import json as _json

    from app.db.models import Document, utcnow
    from app.db.postgres import SessionLocal
    from app.rag import vector_store
    from app.rag.embedding import get_embedder

    with SessionLocal() as db:
        pending = (
            db.query(Document)
            .filter(Document.vector_status == "pending")
            .order_by(Document.created_at.asc())
            .limit(limit)
            .all()
        )
        if not pending:
            return 0, 0
        # 按 doc_id 取整篇（pending 文档的其它块一并重同步，避免删多插少）
        rows = (
            db.query(Document)
            .filter(Document.id.in_([r.id for r in pending]))
            .all()
        )
        groups: dict[tuple[str, str], list] = {}
        for r in rows:
            groups.setdefault((r.user_id, r.source), []).append(r)
        embedder = get_embedder()
        ok = 0
        errors = 0
        for (user_id, source), items in groups.items():
            try:
                chunks = [
                    {"text": r.text, "metadata": _json.loads(r.metadata_json or "{}")}
                    for r in items
                ]
                doc_ids = [r.id for r in items]
                vectors = embedder.embed_texts([c["text"] for c in chunks])
                vector_store.sync_chunks(
                    chunks,
                    doc_ids=doc_ids,
                    source=source,
                    user_id=user_id,
                    vectors=vectors,
                    chunk_indexes=[r.chunk_index for r in items],
                )
                db.query(Document).filter(Document.id.in_(doc_ids)).update(
                    {
                        Document.vector_status: "synced",
                        Document.vector_synced_at: utcnow(),
                    },
                    synchronize_session=False,
                )
                ok += len(items)
            except Exception as exc:
                errors += 1
                logger.warning("pending 向量同步失败 %s@%s: %s", user_id, source, exc)
        db.commit()
        return ok, errors


def _reconcile_sources(max_sources: int = 20) -> tuple[int, int, int]:
    """按 (user, source) 双向 diff：删 Milvus 幽灵 doc_id、补写缺失块。

    以 Postgres 的 (doc_id, chunk_index) 为基准：Milvus 有而 PG 没有 → 幽灵向量删除；
    PG 有而 Milvus 没有 → 重新嵌入补写。返回 (删除幽灵数, 补写块数, 检查源数)。
    """
    import json as _json

    from app.db.models import Document, utcnow
    from app.db.postgres import SessionLocal, distinct_document_sources
    from app.rag import vector_store
    from app.rag.embedding import get_embedder

    pairs = distinct_document_sources()
    ghosts = written = 0
    embedder = get_embedder()
    for user_id, source in pairs[:max_sources]:
        with SessionLocal() as db:
            rows = (
                db.query(Document)
                .filter(Document.source == source, Document.user_id == user_id)
                .all()
            )
        mv = set(vector_store.query_source_pairs(source, user_id=user_id))
        pg = {(r.id, r.chunk_index) for r in rows}
        ghost_ids = {d for d, _ in mv} - {d for d, _ in pg}
        if ghost_ids:
            try:
                vector_store.delete_by_ids(list(ghost_ids))
                ghosts += len(ghost_ids)
            except Exception as exc:
                logger.warning("幽灵向量清理失败 %s@%s: %s", user_id, source, exc)
        missing = pg - mv
        missing_doc_ids = {r.id for r in rows if (r.id, r.chunk_index) in missing}
        if missing_doc_ids:
            # 缺任一块 → 整篇重同步（幂等），避免删多插少
            items = [r for r in rows if r.id in missing_doc_ids]
            try:
                chunks = [
                    {"text": r.text, "metadata": _json.loads(r.metadata_json or "{}")}
                    for r in items
                ]
                doc_ids = [r.id for r in items]
                vectors = embedder.embed_texts([c["text"] for c in chunks])
                vector_store.sync_chunks(
                    chunks,
                    doc_ids=doc_ids,
                    source=source,
                    user_id=user_id,
                    vectors=vectors,
                    chunk_indexes=[r.chunk_index for r in items],
                )
                written += len(items)
                with SessionLocal() as db:
                    db.query(Document).filter(Document.id.in_(doc_ids)).update(
                        {
                            Document.vector_status: "synced",
                            Document.vector_synced_at: utcnow(),
                        },
                        synchronize_session=False,
                    )
                    db.commit()
            except Exception as exc:
                logger.warning("缺失向量补写失败 %s@%s: %s", user_id, source, exc)
    return ghosts, written, min(len(pairs), max_sources)


def _run_reconcile_vectors() -> dict:
    """向量对账任务：pending 行补同步 + (user, source) 双向 diff（事实源=Postgres）。"""
    pending_ok, pending_err = _sync_pending_vectors()
    ghosts, written, sources = _reconcile_sources()
    return {
        "pending_synced": pending_ok,
        "pending_errors": pending_err,
        "ghosts_deleted": ghosts,
        "missing_written": written,
        "sources_checked": sources,
    }


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
    "reconcile_vectors": {
        "label": "向量对账（事实源→派生索引）",
        "desc": "把 pending 行同步到 Milvus，并按 (user, source) 双向 diff 清理幽灵向量 / 补缺失块",
        "fn": _run_reconcile_vectors,
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
