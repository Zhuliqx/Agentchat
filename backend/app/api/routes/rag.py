"""RAG 相关接口：文档上传、检索测试、文档管理。

网页上传的原始文件会**持久保存**到 `data/uploads/<uuid>/` 目录（不再用临时目录），
供下载/预览/审计；删除文档时一并清理。

上传采用**后台任务 + 进度查询**：接口立即返回 task_id，前端轮询
``GET /api/rag/ingest/{task_id}`` 获取摄入进度（读取/分块/嵌入/入库）。
"""
from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import PROJECT_ROOT, settings
from app.api.deps import get_current_user_id
from app.db.postgres import SessionLocal
from app.db.models import Document
from app.rag import vector_store
from app.rag.hybrid import invalidate_docs_signature
from app.rag.ingestion import ingest_file
from app.rag.retriever import get_retriever

router = APIRouter()

ALLOWED_SUFFIX = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
# 项目根下的上传目录（config.upload_dir 相对项目根）
UPLOAD_ROOT = PROJECT_ROOT / settings.upload_dir
# 上传大小上限（字节）
MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024

# 后台摄入任务注册表（进程内；重启清空，前端轮询时 404 即视为任务已消失）
_INGEST_TASKS: dict[str, dict] = {}
_INGEST_LOCK = threading.Lock()
# 最多保留多少条已完成任务（超出后丢弃最旧的，防止内存无限增长）
_MAX_FINISHED_TASKS = 20


def _safe_source_in_uploads(path: Path) -> bool:
    """判断 source 是否位于 uploads 目录内（防任意文件读取）。"""
    try:
        return path.resolve().is_relative_to(UPLOAD_ROOT.resolve())
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
    with _INGEST_LOCK:
        finished = [
            (ts, k)
            for k, v in _INGEST_TASKS.items()
            if v.get("status") in ("done", "error")
            for ts in [v.get("finished_at", 0)]
        ]
        for _, k in sorted(finished)[: max(0, len(finished) - _MAX_FINISHED_TASKS)]:
            _INGEST_TASKS.pop(k, None)


def _run_ingest(task_id: str, dest: Path, filename: str, user_id: str) -> None:
    """后台执行摄入，更新任务注册表中的进度/结果。"""

    def progress(percent: int, stage: str) -> None:
        with _INGEST_LOCK:
            t = _INGEST_TASKS.get(task_id)
            if t:
                t["progress"] = percent
                t["stage"] = stage

    try:
        with _INGEST_LOCK:
            t = _INGEST_TASKS.get(task_id)
            if t:
                t["status"] = "processing"
        result = ingest_file(dest, filename=filename, user_id=user_id, progress_cb=progress)
        with _INGEST_LOCK:
            t = _INGEST_TASKS.get(task_id)
            if t:
                t.update(status="done", progress=100, stage="完成", result=result)
    except Exception as exc:  # 摄入失败：清理原始文件并记录错误
        shutil.rmtree(dest.parent, ignore_errors=True)
        with _INGEST_LOCK:
            t = _INGEST_TASKS.get(task_id)
            if t:
                t.update(status="error", error=str(exc))
    finally:
        import time as _time

        with _INGEST_LOCK:
            t = _INGEST_TASKS.get(task_id)
            if t:
                t["finished_at"] = _time.time()


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
            data = file.file.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"文件过大（上限 {settings.max_upload_mb}MB）")
            dest.write_bytes(data)
        except HTTPException:
            shutil.rmtree(dest_dir, ignore_errors=True)  # 保存失败时清理
            raise
        except Exception as exc:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise HTTPException(500, f"保存文件失败: {exc}")

        task_id = uuid.uuid4().hex
        with _INGEST_LOCK:
            _INGEST_TASKS[task_id] = {
                "status": "pending",
                "progress": 0,
                "stage": "排队中",
                "filename": safe_name,
                "user_id": user_id,
            }
        threading.Thread(
            target=_run_ingest, args=(task_id, dest, safe_name, user_id), daemon=True
        ).start()
        tasks.append({"task_id": task_id, "filename": safe_name, "file_path": str(dest)})
    return {"tasks": tasks}


@router.get("/ingest/{task_id}")
def ingest_status(task_id: str, user_id: str = Depends(get_current_user_id)) -> dict:
    """查询摄入任务进度（仅限任务归属用户）。"""
    with _INGEST_LOCK:
        t = _INGEST_TASKS.get(task_id)
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
            g["has_file"] = _safe_source_in_uploads(Path(d.source))
        if not g["tag"] and d.tag:
            g["tag"] = d.tag
    return list(grouped.values())


@router.get("/documents/file")
def get_document_file(
    source: str,
    download: bool = False,
    user_id: str = Depends(get_current_user_id),
):
    """获取上传文档的原始文件（仅限当前用户 + uploads 目录内，防越权/任意文件读取）。

    - download=false：内联预览（文本类可读内容）
    - download=true：强制下载
    """
    path = Path(source)
    if not _safe_source_in_uploads(path) or not path.is_file():
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
    return FileResponse(
        path,
        filename=path.name,
        media_type=_guess_media(path),
        content_disposition_type="attachment" if download else "inline",
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
    invalidate_docs_signature()
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
