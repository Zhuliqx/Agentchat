"""RAG 相关接口：文档上传、检索测试、文档管理。

网页上传的原始文件会**持久保存**到 `data/uploads/<uuid>/` 目录（不再用临时目录），
供下载/预览/审计；删除文档时一并清理。
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.config import settings
from app.api.deps import get_current_user_id
from app.db.postgres import SessionLocal
from app.db.models import Document
from app.rag import vector_store
from app.rag.ingestion import ingest_file
from app.rag.retriever import get_retriever

router = APIRouter()

ALLOWED_SUFFIX = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
# 项目根（rag.py -> routes -> api -> app -> backend -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
# 项目根下的上传目录（config.upload_dir 相对项目根）
UPLOAD_ROOT = PROJECT_ROOT / settings.upload_dir
# 上传大小上限（字节）
MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024


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


@router.post("/upload")
def upload_document(
    file: UploadFile = File(...),
    request: Request = None,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """上传文档：持久保存原始文件到 data/uploads/<uuid>/ 并摄入到向量库（归属当前用户）。"""
    # 大小限制：优先用 Content-Length 快速拒绝，读后二次校验
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件过大（上限 {settings.max_upload_mb}MB）")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(415, f"不支持的文件类型 {suffix or '未知'}，支持: {sorted(ALLOWED_SUFFIX)}")

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
        result = ingest_file(dest, filename=safe_name, user_id=user_id)
    except HTTPException:
        shutil.rmtree(dest_dir, ignore_errors=True)  # 失败时清理原始文件
        raise
    except Exception as exc:
        shutil.rmtree(dest_dir, ignore_errors=True)  # 失败时清理原始文件
        raise HTTPException(500, f"摄入失败: {exc}")
    result["file_path"] = str(dest)
    return result


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
                "created_at": d.created_at.isoformat(),
                "has_file": False,  # 原始文件是否可在线查看/下载
            },
        )
        g["chunks"] += 1
        if not g["has_file"]:
            g["has_file"] = _safe_source_in_uploads(Path(d.source))
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


@router.delete("/documents")
def delete_document(
    source: str, user_id: str = Depends(get_current_user_id)
) -> dict:
    """按 source 删除当前用户的文档（向量 + 元数据 + uploads 内原始文件）。"""
    vector_store.delete_by_source(source, user_id=user_id)
    with SessionLocal() as db:
        deleted = (
            db.query(Document)
            .filter(Document.source == source, Document.user_id == user_id)
            .delete()
        )
        db.commit()
    # 失效文档集签名缓存（使 BM25 关键词通道立即排除被删文档）
    try:
        from app.rag.hybrid import invalidate_docs_signature

        invalidate_docs_signature()
    except Exception:
        pass
    # 原始文件在 uploads 内时一并删除（整个 <uuid>/ 目录）
    path = Path(source)
    if _safe_source_in_uploads(path):
        shutil.rmtree(path.parent, ignore_errors=True)
    return {"source": source, "deleted_chunks": deleted}


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
