"""会话管理接口。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.graph import list_checkpoint_history
from app.api.deps import get_current_user_id
from app.db import postgres
from app.db.models import Message
from app.db.postgres import SessionLocal

router = APIRouter()


class SessionOut(BaseModel):
    id: str
    title: str
    pinned: bool = False
    created_at: str
    updated_at: str


class SourceRefOut(BaseModel):
    """引用溯源条目：path + 命中片段数（老数据只有路径字符串）。"""

    path: str
    hits: int | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    # 兼容两种历史数据：新记录是 {path, hits}，老记录是纯路径字符串
    sources: list[str | SourceRefOut] = []
    created_at: str | None = None  # 前端用于消息时间戳（hover 显示）


class RenameIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    pinned: bool | None = None


class TruncateIn(BaseModel):
    """从某条消息截断：删除该消息及其之后的全部消息（编辑重发用）。"""

    message_id: str


class BatchDeleteIn(BaseModel):
    """批量删除会话请求体。"""

    ids: list[str] = Field(..., min_length=1, max_length=500)


def _session_out(s):
    return SessionOut(
        id=s.id,
        title=s.title,
        pinned=bool(s.pinned),
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
    )


def _cleanup_checkpoints(thread_ids: list[str]) -> None:
    """定向清理已删除会话对应的孤儿 checkpoint（避免 DB 膨胀）。"""
    if not thread_ids:
        return
    try:
        from app.db.memory_store import cleanup_stale_checkpoints

        cleanup_stale_checkpoints(thread_ids=thread_ids)
    except Exception:
        pass


def _owned_or_404(session_id: str, user_id: str):
    """读取会话并校验归属（他人会话返回 404，避免泄露存在性）。"""
    s = postgres.get_owned_session(session_id, user_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    return s


@router.get("", response_model=list[SessionOut])
def list_sessions(
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user_id),
):
    """默认返回该用户全部会话（侧栏不设显示上限）；传 limit 时才分页。"""
    return [
        _session_out(s)
        for s in postgres.list_sessions(user_id=user_id, limit=limit, offset=offset)
    ]


@router.post("", response_model=SessionOut, status_code=201)
def create_session(user_id: str = Depends(get_current_user_id)):
    s = postgres.create_session(user_id=user_id)
    return _session_out(s)


@router.post("/batch-delete")
def batch_delete(
    body: BatchDeleteIn, user_id: str = Depends(get_current_user_id)
) -> dict:
    """批量删除会话（含消息与对应 LangGraph checkpoint），返回删除数量。

    存在不存在的 id 时静默跳过；删除后定向清理这些会话的 checkpoint，避免 DB 膨胀。
    """
    deleted: list[str] = []
    for sid in body.ids:
        s = postgres.get_session(sid)
        if s and s.user_id == user_id and postgres.delete_session(sid):
            deleted.append(sid)
    # 定向清理已删除会话对应的孤儿 checkpoint（避免全表扫）
    _cleanup_checkpoints(deleted)
    return {"deleted": len(deleted), "requested": len(body.ids)}


@router.get("/{session_id}", response_model=list[MessageOut])
def get_history(session_id: str, user_id: str = Depends(get_current_user_id)):
    _owned_or_404(session_id, user_id)
    msgs = postgres.get_messages(session_id)
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            sources=m.sources or [],
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in msgs
    ]


@router.delete("/{session_id}/messages/{message_id}", status_code=204)
def delete_message(
    session_id: str, message_id: str, user_id: str = Depends(get_current_user_id)
):
    """删除会话中的单条消息（仅限当前用户；不影响 LangGraph checkpoint 历史）。"""
    _owned_or_404(session_id, user_id)
    with SessionLocal() as db:
        m = db.get(Message, message_id)
        if not m or m.session_id != session_id:
            raise HTTPException(404, "消息不存在")
        db.delete(m)
        db.commit()


@router.post("/{session_id}/truncate")
def truncate_messages(
    session_id: str,
    body: TruncateIn,
    user_id: str = Depends(get_current_user_id),
):
    """从指定消息截断：删除该消息及其之后的全部消息（一个事务内完成）。

    编辑重发用它替代"前端逐条删除"：既避免中途失败留下半截历史，
    也让删除范围由服务端按时间点界定，不受前端本地列表状态影响。
    """
    _owned_or_404(session_id, user_id)
    try:
        deleted = postgres.truncate_messages_from(session_id, body.message_id)
    except ValueError:
        raise HTTPException(404, "消息不存在") from None
    return {"deleted": deleted}


@router.get("/{session_id}/stats")
def session_stats(
    session_id: str,
    tz_offset_min: int = Query(0, ge=-840, le=840),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """会话深度分析：消息统计、时长、token 估算、应答耗时、活跃时段与引用来源。

    tz_offset_min：客户端相对 UTC 的分钟偏移（东八区为 480）。用它把 UTC 时间折算成
    本地小时再统计"活跃时段"，否则热力条会整体错一个时区。
    """
    _owned_or_404(session_id, user_id)
    msgs = postgres.get_messages(session_id)
    total_chars = sum(len(m.content) for m in msgs)
    user_msgs = [m for m in msgs if m.role == "user"]
    asst_msgs = [m for m in msgs if m.role == "assistant"]
    sys_msgs = [m for m in msgs if m.role == "system"]

    # 回合数：每条 user 消息视为一个回合（连续 user 消息合并计数一次）
    rounds = 0
    prev_user = False
    for m in msgs:
        if m.role == "user":
            if not prev_user:
                rounds += 1
            prev_user = True
        else:
            prev_user = False

    def _chars(lst):
        return sum(len(m.content) for m in lst)

    longest_response = max((len(m.content) for m in asst_msgs), default=0)

    first_at = msgs[0].created_at if msgs else None
    last_at = msgs[-1].created_at if msgs else None
    duration_sec = None
    if first_at and last_at:
        duration_sec = max(0, int((last_at - first_at).total_seconds()))

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    # 应答耗时：用户发言 → 紧随其后的助手回复，取平均（秒）
    gaps: list[float] = []
    pending_user_at: datetime | None = None
    for m in msgs:
        if m.role == "user":
            pending_user_at = m.created_at
        elif m.role == "assistant" and pending_user_at and m.created_at:
            gaps.append((m.created_at - pending_user_at).total_seconds())
            pending_user_at = None
    avg_response_sec = round(sum(gaps) / len(gaps), 1) if gaps else None

    # 活跃时段：按客户端本地小时统计消息条数
    tz = timezone(timedelta(minutes=tz_offset_min))
    hourly_counts = [0] * 24
    for m in msgs:
        if m.created_at:
            hourly_counts[m.created_at.astimezone(tz).hour] += 1

    # 引用来源：聚合助手消息里的 sources（新格式 {path,hits}；老格式为纯路径字符串）
    source_stats: dict[str, dict] = {}
    for m in asst_msgs:
        for s in m.sources or []:
            path = s.get("path") if isinstance(s, dict) else str(s)
            hits = int(s.get("hits") or 0) if isinstance(s, dict) else 0
            if not path:
                continue
            item = source_stats.setdefault(path, {"path": path, "messages": 0, "hits": 0})
            item["messages"] += 1
            item["hits"] += hits
    top_sources = sorted(
        source_stats.values(), key=lambda x: (-x["hits"], -x["messages"], x["path"])
    )[:5]

    return {
        "session_id": session_id,
        "message_count": len(msgs),
        "user_count": len(user_msgs),
        "assistant_count": len(asst_msgs),
        "system_count": len(sys_msgs),
        "rounds": rounds,
        "total_chars": total_chars,
        "est_tokens": max(1, round(total_chars / 1.7)),  # 中英混合粗略估算
        "avg_user_chars": round(_chars(user_msgs) / len(user_msgs), 1) if user_msgs else 0,
        "avg_assistant_chars": round(_chars(asst_msgs) / len(asst_msgs), 1) if asst_msgs else 0,
        "longest_response_chars": longest_response,
        "first_at": _iso(first_at),
        "last_at": _iso(last_at),
        "duration_sec": duration_sec,
        "avg_response_sec": avg_response_sec,
        "hourly_counts": hourly_counts,
        "top_sources": top_sources,
    }


@router.get("/{session_id}/export")
def export_session(session_id: str, user_id: str = Depends(get_current_user_id)) -> dict:
    """导出会话为 Markdown（对话记录），供下载/归档。"""
    _owned_or_404(session_id, user_id)
    msgs = postgres.get_messages(session_id)
    lines: list[str] = []
    for m in msgs:
        role = "用户" if m.role == "user" else "助手"
        lines.append(f"## {role}\n\n{m.content}\n")
    return {"session_id": session_id, "markdown": "\n".join(lines)}


@router.get("/{session_id}/checkpoints")
async def list_checkpoints(
    session_id: str, user_id: str = Depends(get_current_user_id)
):
    """Time Travel：列出会话的 LangGraph checkpoint 历史（新→旧）。

    每条含 checkpoint_id（可传给 /api/chat 的 checkpoint_id 实现回退/分叉）、
    创建时间、下一步节点、最后 AI 消息摘要、是否处于人工确认等待中。
    """
    _owned_or_404(session_id, user_id)
    return await list_checkpoint_history(session_id)


@router.patch("/{session_id}", response_model=SessionOut)
def rename(
    session_id: str, body: RenameIn, user_id: str = Depends(get_current_user_id)
):
    _owned_or_404(session_id, user_id)
    if body.title is None and body.pinned is None:
        raise HTTPException(400, "缺少要修改的字段")
    s = postgres.rename_session(session_id, title=body.title, pinned=body.pinned)
    if not s:
        raise HTTPException(404, "会话不存在")
    return _session_out(s)


@router.delete("/{session_id}", status_code=204)
def delete(session_id: str, user_id: str = Depends(get_current_user_id)):
    _owned_or_404(session_id, user_id)
    postgres.delete_session(session_id)
    # 定向清理该会话的 LangGraph checkpoint（与 batch-delete 保持一致）
    _cleanup_checkpoints([session_id])
