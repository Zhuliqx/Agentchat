"""全局搜索：跨当前用户的会话标题与消息内容（LIKE 模糊匹配）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.db.models import Message, Session
from app.db.postgres import SessionLocal

router = APIRouter()


def _snippet(text: str, q: str, radius: int = 60) -> str:
    """定位关键词并截取前后文片段（最长约 radius*2 字符）。"""
    idx = text.lower().find(q.lower())
    if idx < 0:
        return text[: 2 * radius] + ("…" if len(text) > 2 * radius else "")
    start = max(0, idx - radius)
    end = min(len(text), idx + len(q) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


@router.get("")
def search(q: str, user_id: str = Depends(get_current_user_id)) -> dict:
    """搜索当前用户的会话标题与消息内容，返回命中会话与消息片段。"""
    q = q.strip()
    if not q:
        return {"sessions": [], "messages": []}
    like = f"%{q}%"
    with SessionLocal() as db:
        sessions = (
            db.query(Session)
            .filter(Session.user_id == user_id, Session.title.ilike(like))
            .order_by(Session.updated_at.desc())
            .limit(10)
            .all()
        )
        session_out = [
            {
                "id": s.id,
                "title": s.title,
                "pinned": bool(s.pinned),
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ]
        rows = (
            db.query(Message, Session)
            .join(Session, Message.session_id == Session.id)
            .filter(Session.user_id == user_id, Message.content.ilike(like))
            .order_by(Message.created_at.desc())
            .limit(20)
            .all()
        )
        msg_out = [
            {
                "session_id": s.id,
                "session_title": s.title,
                "message_id": m.id,
                "role": m.role,
                "content": _snippet(m.content, q),
                "created_at": m.created_at.isoformat(),
            }
            for m, s in rows
        ]
    return {"sessions": session_out, "messages": msg_out}
