"""RAG 相关接口：文档上传、检索测试、文档管理。

网页上传的原始文件会**持久保存**到 `data/uploads/<uuid>/` 目录（不再用临时目录），
供下载/预览/审计；删除文档时一并清理。

上传采用**后台任务 + 进度查询**：接口立即返回 task_id，前端轮询
``GET /api/rag/ingest/{task_id}`` 获取摄入进度（读取/分块/嵌入/入库）。
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import get_current_user_id
from app.cache.redis_client import get_redis, redis_key
from app.config import PROJECT_ROOT, settings
from app.db.postgres import SessionLocal
from app.db.models import Document
from app.rag import vector_store
from app.rag.hybrid import invalidate_docs_signature
from app.rag.ingestion import ingest_file
from app.rag.retriever import get_retriever

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SUFFIX = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
# 项目根下的上传目录（config.upload_dir 相对项目根）
UPLOAD_ROOT = PROJECT_ROOT / settings.upload_dir
# 内置知识库随应用分发（data/kb/*.md），不属于用户上传，但同样需要能预览原文
KB_ROOT = PROJECT_ROOT / "data" / "kb"
# 上传大小上限（字节）
MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024
_UPLOAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(settings.upload_max_concurrency or 2)),
    thread_name_prefix="rag-ingest",
)

# 摄入任务注册表：内存保存最新状态；REDIS_ENABLED=true 时同步 Redis 快照
#（TTL 24h），多 worker 下任意进程都能查到进度。重启后执行中的任务无法继续，
# Redis 快照会随 TTL 过期，与“任务已消失”语义一致。
_INGEST_TASKS: dict[str, dict] = {}
_INGEST_LOCK = threading.Lock()
# 最多保留多少条已完成任务（超出后丢弃最旧的，防止内存无限增长）
_MAX_FINISHED_TASKS = 20
_INGEST_TASK_TTL_SEC = 24 * 60 * 60


def _safe_source_in_uploads(path: Path) -> bool:
    """判断 source 是否位于 uploads 目录内（删除原始文件时用，必须是用户上传目录）。"""
    try:
        return path.resolve().is_relative_to(UPLOAD_ROOT.resolve())
    except (ValueError, AttributeError):  # pragma: no cover
        return False


def _safe_inline_source(path: Path) -> bool:
    """判断 source 是否位于可内联读取的目录（用户上传目录 / 内置知识库）。

    白名单放在服务端：即使 source 字符串来自请求，也不能读到这两个目录之外
    （路由另有"该 source 属于当前用户"的归属校验，两层一起挡住越权读取）。
    注意：删除原始文件仍只认 uploads（见 _safe_source_in_uploads），避免误删内置知识库。
    """
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(UPLOAD_ROOT.resolve()) or resolved.is_relative_to(
            KB_ROOT.resolve()
        )
    except (ValueError, AttributeError):  # pragma: no cover
        return False


def _guess_media(path: Path) -> str:
    """按扩展名猜测媒体类型（用于文件预览/下载）。"""
    return {
        ".pdf": "application/pdf",
        ".docx": (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
    }.get(path.suffix.lower(), "text/plain; charset=utf-8")


def _prune_finished_tasks() -> None:
    """丢弃最旧的已完成任务（内存上限保护）。"""
    # Redis 端由 TTL 自动清理，这里只限制本进程内存占用
    with _INGEST_LOCK:
        finished = [
            (ts, k)
            for k, v in _INGEST_TASKS.items()
            if v.get("status") in ("done", "error")
            for ts in [v.get("finished_at", 0)]
        ]
        for _, k in sorted(finished)[: max(0, len(finished) - _MAX_FINISHED_TASKS)]:
            _INGEST_TASKS.pop(k, None)


def _ingest_task_key(task_id: str) -> str:
    """摄入任务状态的 Redis key（带统一前缀与业务域）。"""
    return redis_key("ingest", "task", task_id)


def _sync_ingest_task_to_redis(task_id: str) -> None:
    """把内存中的最新快照写入 Redis（保留 TTL 24h）。"""
    with _INGEST_LOCK:
        task = _INGEST_TASKS.get(task_id)
        snapshot = dict(task) if task is not None else None
    if snapshot is None:
        return
    client = get_redis()
    if client is None:
        return
    try:
        client.set(
            _ingest_task_key(task_id),
            json.dumps(snapshot, ensure_ascii=False),
            ex=_INGEST_TASK_TTL_SEC,
        )
    except Exception:  # noqa: BLE001
        pass  # Redis 故障时保留内存状态；降级状态由健康检查体现


def _create_ingest_task(task_id: str, task: dict) -> None:
    """登记新任务：先写内存，再同步 Redis 供其他 worker 查询。"""
    with _INGEST_LOCK:
        _INGEST_TASKS[task_id] = task
    _sync_ingest_task_to_redis(task_id)


def _update_ingest_task(task_id: str, **updates: object) -> None:
    """更新任务字段，并把最新快照同步到 Redis。"""
    with _INGEST_LOCK:
        task = _INGEST_TASKS.get(task_id)
        if task is not None:
            task.update(updates)
    _sync_ingest_task_to_redis(task_id)


def _read_ingest_task(task_id: str) -> dict | None:
    """读取任务状态：Redis 优先（跨 worker），缺失/故障时回退本进程内存。"""
    client = get_redis()
    if client is not None:
        try:
            raw = client.get(_ingest_task_key(task_id))
            if raw is not None:
                return json.loads(raw)
        except Exception:  # noqa: BLE001
            pass
    with _INGEST_LOCK:
        task = _INGEST_TASKS.get(task_id)
        return dict(task) if task is not None else None


def _run_ingest(task_id: str, dest: Path, filename: str, user_id: str) -> None:
    """后台执行摄入，更新任务注册表中的进度/结果。"""

    def progress(percent: int, stage: str) -> None:
        _update_ingest_task(task_id, progress=percent, stage=stage)

    try:
        _update_ingest_task(task_id, status="processing")
        result = ingest_file(dest, filename=filename, user_id=user_id, progress_cb=progress)
        _update_ingest_task(
            task_id,
            status="done",
            progress=100,
            stage="完成",
            result=result,
            finished_at=time.time(),
        )
    except Exception as exc:  # 摄入失败：清理原始文件并记录错误
        shutil.rmtree(dest.parent, ignore_errors=True)
        _update_ingest_task(
            task_id,
            status="error",
            error=str(exc),
            finished_at=time.time(),
        )


def _save_upload_stream(file, dest: Path, max_bytes: int) -> None:
    """流式落盘并限制大小（不整读进内存，超限即抛 413）。"""
    _CHUNK = 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                data = file.file.read(_CHUNK)
                if not data:
                    break
                written += len(data)
                if written > max_bytes:
                    raise HTTPException(
                        413, f"文件过大（上限 {settings.max_upload_mb}MB）"
                    )
                out.write(data)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise


@router.post("/upload")
def upload_document(
    files: list[UploadFile] = File(..., alias="file"),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """上传一个或多个文档：保存原始文件后后台摄入，立即返回任务列表。

    返回 ``{"tasks": [{task_id, filename, file_path}]}``，前端轮询
    ``GET /api/rag/ingest/{task_id}`` 获取进度。
    """
    _prune_finished_tasks()
    tasks = []
    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIX:
            raise HTTPException(
                415,
                f"不支持的文件类型 {suffix or '未知'}，支持: {sorted(ALLOWED_SUFFIX)}",
            )

        # 仅取文件名，防路径穿越；保存到 uploads/<uuid>/ 目录（持久保留）
        safe_name = Path(file.filename or "upload").name
        dest_dir = UPLOAD_ROOT / uuid.uuid4().hex
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        try:
            _save_upload_stream(file, dest, MAX_UPLOAD_BYTES)
        except HTTPException:
            shutil.rmtree(dest_dir, ignore_errors=True)  # 保存失败时清理
            raise
        except Exception as exc:
            shutil.rmtree(dest_dir, ignore_errors=True)
            logger.exception("上传文件保存失败: %s", exc)
            raise HTTPException(500, "文件保存失败，请稍后重试")

        task_id = uuid.uuid4().hex
        _create_ingest_task(
            task_id,
            {
                "status": "pending",
                "progress": 0,
                "stage": "排队中",
                "filename": safe_name,
                "user_id": user_id,
            },
        )
        _UPLOAD_EXECUTOR.submit(_run_ingest, task_id, dest, safe_name, user_id)
        tasks.append({"task_id": task_id, "filename": safe_name, "file_path": str(dest)})
    return {"tasks": tasks}


@router.get("/ingest/{task_id}")
def ingest_status(task_id: str, user_id: str = Depends(get_current_user_id)) -> dict:
    """查询摄入任务进度（仅限任务归属用户）。"""
    t = _read_ingest_task(task_id)
    if not t or t.get("user_id") != user_id:
        raise HTTPException(404, "任务不存在或已过期")
    return {
        "status": t.get("status"),
        "progress": t.get("progress", 0),
        "stage": t.get("stage", ""),
        "filename": t.get("filename"),
        "result": t.get("result"),
        "error": t.get("error"),
    }


@router.post("/search")
def search_docs(
    query: str, top_k: int = 4, user_id: str = Depends(get_current_user_id)
) -> dict:
    """检索测试接口（与 RAG Agent 完全同路径：混合检索 + rerank + 去重合并 + 截断，限定当前用户）。"""
    retriever = get_retriever(user_id=user_id)
    retriever.top_k = top_k
    docs = retriever.invoke(query)
    hits = [
        {
            "text": d.page_content,
            "source": d.metadata.get("source", ""),
            "chunk_index": d.metadata.get("chunk_index"),
            "score": d.metadata.get("score"),
            "rrf_score": d.metadata.get("rrf_score"),
            "rerank_score": d.metadata.get("rerank_score"),
        }
        for d in docs
    ]
    return {"query": query, "hits": hits}


@router.get("/documents")
def list_documents(user_id: str = Depends(get_current_user_id)) -> list[dict]:
    """列出当前用户已摄入的文档（按 source 去重汇总）。"""
    with SessionLocal() as db:
        stmt = (
            select(Document)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
        )
        docs = db.scalars(stmt).all()

    # 按 source 聚合
    grouped: dict[str, dict] = {}
    for d in docs:
        g = grouped.setdefault(
            d.source,
            {
                "source": d.source,
                "filename": d.filename,
                "chunks": 0,
                "tag": d.tag,
                "created_at": d.created_at.isoformat(),
                "has_file": False,  # 原始文件是否可在线查看/下载
            },
        )
        g["chunks"] += 1
        if not g["has_file"]:
            g["has_file"] = _safe_inline_source(Path(d.source))
        if not g["tag"] and d.tag:
            g["tag"] = d.tag
    return list(grouped.values())


@router.get("/documents/file")
def get_document_file(
    source: str,
    download: bool = False,
    user_id: str = Depends(get_current_user_id),
):
    """获取文档原始文件（仅限当前用户 + uploads 或内置知识库目录，防越权/任意文件读取）。

    - download=false：内联预览（文本类可读内容）
    - download=true：强制下载
    """
    path = Path(source)
    if not _safe_inline_source(path) or not path.is_file():
        raise HTTPException(404, "原始文件不存在或不可访问")
    # 越权校验：该 source 必须属于当前用户
    with SessionLocal() as db:
        owned = (
            db.query(Document.id)
            .filter(Document.source == source, Document.user_id == user_id)
            .first()
        )
    if not owned:
        raise HTTPException(404, "原始文件不存在或不可访问")
    # 同源内联预览 HTML 时强制沙箱：阻断脚本执行，防上传型存储 XSS。
    extra_headers = {}
    if not download and path.suffix.lower() in {".html", ".htm"}:
        extra_headers = {
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        }
    return FileResponse(
        path,
        filename=path.name,
        media_type=_guess_media(path),
        content_disposition_type="attachment" if download else "inline",
        headers=extra_headers,
    )


def _delete_document_by_source(source: str, user_id: str) -> dict:
    """删除单个文档（向量 + Postgres 元数据 + uploads 内原始文件），供单删/批量复用。"""
    vector_store.delete_by_source(source, user_id=user_id)
    with SessionLocal() as db:
        deleted = (
            db.query(Document)
            .filter(Document.source == source, Document.user_id == user_id)
            .delete()
        )
        db.commit()
    # 失效文档集签名缓存（使 BM25 关键词通道立即排除被删文档）
    invalidate_docs_signature(user_id)
    # 原始文件在 uploads 内时一并删除（整个 <uuid>/ 目录）
    path = Path(source)
    if _safe_source_in_uploads(path):
        shutil.rmtree(path.parent, ignore_errors=True)
    return {"source": source, "deleted_chunks": deleted}


class BatchDeleteDocsIn(BaseModel):
    """批量删除文档请求体。"""

    sources: list[str] = Field(..., min_length=1, max_length=200)


class DocumentTagIn(BaseModel):
    """设置文档标签请求体（tag 传 null/空串清除）。"""

    source: str = Field(..., max_length=500)
    tag: str | None = Field(default=None, max_length=50)


@router.patch("/documents/tag")
def set_document_tag(
    body: DocumentTagIn, user_id: str = Depends(get_current_user_id)
) -> dict:
    """设置/清除文档标签（同步更新该 source 的所有分块）。"""
    tag = (body.tag or "").strip() or None
    with SessionLocal() as db:
        n = (
            db.query(Document)
            .filter(
                Document.source == body.source, Document.user_id == user_id
            )
            .update({Document.tag: tag}, synchronize_session=False)
        )
        db.commit()
    return {"source": body.source, "tag": tag, "updated": n}


@router.post("/documents/batch-delete")
def batch_delete_documents(
    body: BatchDeleteDocsIn, user_id: str = Depends(get_current_user_id)
) -> dict:
    """批量删除文档（逐项调用公共删除逻辑，source 去重保序）。"""
    items = [
        _delete_document_by_source(s, user_id)
        for s in dict.fromkeys(body.sources)
    ]
    return {"deleted": len(items), "items": items}


@router.delete("/documents")
def delete_document(
    source: str, user_id: str = Depends(get_current_user_id)
) -> dict:
    """按 source 删除当前用户的文档（向量 + 元数据 + uploads 内原始文件）。"""
    return _delete_document_by_source(source, user_id)


@router.get("/retriever")
def test_retriever(query: str, user_id: str = Depends(get_current_user_id)) -> dict:
    """测试 LangChain 检索器（限定当前用户知识库）。"""
    retriever = get_retriever(user_id=user_id)
    docs = retriever.invoke(query)
    return {
        "query": query,
        "count": len(docs),
        "documents": [
            {"text": d.page_content, "metadata": d.metadata} for d in docs
        ],
    }
