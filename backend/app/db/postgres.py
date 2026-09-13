"""Postgres 连接与会话管理。

使用 SQLAlchemy 2.x 同步引擎；对话历史读写集中在这里。
"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker

from app.config import BASE_DIR, settings
from app.db.models import Document, Message, Session, Task, User, utcnow

engine = create_engine(
    settings.postgres_dsn,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """应用启动后的最小初始化：确保内置访客用户存在。

    表结构由 Alembic 全权管理（见 run_migrations），本函数不再执行任何 DDL。
    """
    _ensure_guest_user()


def run_migrations() -> bool:
    """执行 Alembic 迁移到最新版本；用 advisory lock 防止多进程并发 DDL。

    返回是否由本进程执行（未抢到锁时跳过，等待下一个进程执行）。
    """
    from sqlalchemy import text

    lock_key = 730255
    try:
        with engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
            ).scalar()
            if not acquired:
                return False
            try:
                from alembic import command
                from alembic.config import Config

                cfg = Config(str(BASE_DIR / "alembic.ini"))
                # 应用内跑迁移时不重配 logging，避免 alembic.ini 吞掉启动日志
                cfg.attributes["configure_logging"] = False
                command.upgrade(cfg, "head")
                return True
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
                )
    except Exception:
        raise


def _ensure_guest_user() -> None:
    import secrets

    from app.security import hash_password

    with SessionLocal() as db:
        if db.get(User, settings.guest_user_id) is None:
            db.add(
                User(
                    id=settings.guest_user_id,
                    username=settings.guest_user_id,
                    password_hash=hash_password(secrets.token_urlsafe(24)),
                )
            )
            db.commit()


# ---------------- 用户管理 ----------------

def create_user(
    username: str, password_hash: str, avatar_color: str = "accent"
) -> User:
    with SessionLocal() as db:
        u = User(
            username=username,
            password_hash=password_hash,
            avatar_color=avatar_color,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u


def get_user_by_username(username: str) -> User | None:
    with SessionLocal() as db:
        return db.scalars(select(User).where(User.username == username)).first()


def get_user(user_id: str) -> User | None:
    with SessionLocal() as db:
        return db.get(User, user_id)


def delete_user(user_id: str) -> bool:
    """删除用户及其级联数据（sessions / messages）。"""
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if not u:
            return False
        db.delete(u)
        db.commit()
        return True


def has_registered_users() -> bool:
    """是否存在除内置访客外的真实注册用户（用于平台操作员鉴权）。"""
    with SessionLocal() as db:
        return (
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.id != settings.guest_user_id)
            )
            or 0
        ) > 0


def update_user(
    user_id: str,
    *,
    username: str | None = None,
    password_hash: str | None = None,
    avatar_color: str | None = None,
) -> User | None:
    """更新用户名/密码哈希/头像颜色（传 None 的字段保持不变）。"""
    with SessionLocal() as db:
        u = db.get(User, user_id)
        if not u:
            return None
        if username is not None:
            u.username = username
        if password_hash is not None:
            u.password_hash = password_hash
        if avatar_color is not None:
            u.avatar_color = avatar_color
        db.commit()
        db.refresh(u)
        return u


# ---------------- 用户统计（个人主页） ----------------

def count_sessions(user_id: str) -> int:
    with SessionLocal() as db:
        return (
            db.scalar(
                select(func.count()).select_from(Session).where(Session.user_id == user_id)
            )
            or 0
        )


def count_messages_for_user(user_id: str) -> int:
    """统计某用户所有会话的消息总数。"""
    with SessionLocal() as db:
        return (
            db.scalar(
                select(func.count(Message.id))
                .join(Session, Message.session_id == Session.id)
                .where(Session.user_id == user_id)
            )
            or 0
        )


def count_documents() -> int:
    """统计知识库文档分块总数（全局共享）。"""
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Document)) or 0


def distinct_document_sources() -> list[tuple[str, str]]:
    """所有 (user_id, source) 文档组合（source 非空）。"""
    with SessionLocal() as db:
        rows = db.execute(
            select(Document.user_id, Document.source).distinct()
        ).all()
    return [(u, s) for u, s in rows if s]


# ---------------- 会话管理 ----------------

def create_session(title: str = "新会话", user_id: str | None = None) -> Session:
    with SessionLocal() as db:
        s = Session(title=title, user_id=user_id or settings.guest_user_id)
        db.add(s)
        db.commit()
        db.refresh(s)
        return s


def get_session(session_id: str) -> Session | None:
    with SessionLocal() as db:
        return db.get(Session, session_id)


def get_owned_session(session_id: str, user_id: str) -> Session | None:
    """读取会话并校验归属：不存在或非本人返回 None（避免泄露存在性）。"""
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        return s if s and s.user_id == user_id else None


def list_sessions(
    user_id: str | None = None, limit: int | None = None, offset: int = 0
) -> list[Session]:
    with SessionLocal() as db:
        stmt = select(Session).order_by(
            Session.pinned.desc(), Session.updated_at.desc()
        )
        if user_id:
            stmt = stmt.where(Session.user_id == user_id)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(db.scalars(stmt))


def rename_session(
    session_id: str, title: str | None = None, pinned: bool | None = None
) -> Session | None:
    """重命名 / 置顶。置顶不算内容更新：显式写回原 updated_at，
    否则列表（按 updated_at 倒序）会把取消置顶的会话顶到最前，回不到原位。"""
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            return None
        if title is None and pinned is not None:
            db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(pinned=pinned, updated_at=Session.updated_at)
            )
            db.commit()
            db.refresh(s)
            return s
        if title is not None:
            s.title = title
        if pinned is not None:
            s.pinned = pinned
        db.commit()
        db.refresh(s)
        return s


def delete_session(session_id: str) -> bool:
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            return False
        db.delete(s)
        db.commit()
        return True


# ---------------- 消息管理 ----------------

def add_message(
    session_id: str,
    role: str,
    content: str,
    sources: list[str] | None = None,
) -> Message:
    with SessionLocal() as db:
        msg = Message(
            session_id=session_id, role=role, content=content, sources=sources
        )
        db.add(msg)
        # 会话标题：取第一条用户消息前 20 字；并显式刷新 updated_at。
        # onupdate 只在 UPDATE 语句触发——不改标题时不会刷新，导致活跃会话排后面。
        s = db.get(Session, session_id)
        if s is not None:
            if s.title == "新会话" and role == "user":
                s.title = content[:20]
            s.updated_at = utcnow()
        db.commit()
        db.refresh(msg)
        return msg


def get_messages(session_id: str, limit: int = 50) -> list[Message]:
    with SessionLocal() as db:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt))


def truncate_messages_from(session_id: str, message_id: str) -> int:
    """删除该消息**及其之后**的全部消息，返回删除条数。

    编辑重发要把"从这条消息起的历史"整体丢掉：逐条 DELETE 一旦中途失败就会
    留下半截历史，所以放到一个事务里按时间点一次性删。
    """
    with SessionLocal() as db:
        target = db.get(Message, message_id)
        if not target or target.session_id != session_id:
            raise ValueError("消息不存在")
        result = db.query(Message).filter(
            Message.session_id == session_id,
            Message.created_at >= target.created_at,
        )
        deleted = result.delete(synchronize_session=False)
        db.commit()
        return int(deleted)


def get_recent_messages(session_id: str, limit: int = 50) -> list[Message]:
    """读取某会话**最近** limit 条消息（按时间正序返回）。

    与 get_messages 的差异：get_messages 取的是「最早 limit 条」（历史展示
    场景按时间正序翻页），这里取**最新** limit 条再倒序回正——检索上文/
    多轮上下文（RAG_MULTI_TURN_CONTEXT）需要最近几轮，不能用最早几条。
    """
    with SessionLocal() as db:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(reversed(db.scalars(stmt).all()))


# ---------------- 定时任务管理 ----------------

def create_task(name: str, task_type: str, schedule: str) -> Task:
    with SessionLocal() as db:
        t = Task(name=name, task_type=task_type, schedule=schedule)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t


def list_tasks() -> list[Task]:
    with SessionLocal() as db:
        return list(db.scalars(select(Task).order_by(Task.created_at.asc())))


def get_task(task_id: str) -> Task | None:
    with SessionLocal() as db:
        return db.get(Task, task_id)


def update_task(
    task_id: str,
    name: str | None = None,
    schedule: str | None = None,
    enabled: bool | None = None,
) -> Task | None:
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            return None
        if name is not None:
            t.name = name
        if schedule is not None:
            t.schedule = schedule
        if enabled is not None:
            t.enabled = enabled
        t.next_run_at = None  # 调度变更后由调度器重新计算
        db.commit()
        db.refresh(t)
        return t


def delete_task(task_id: str) -> bool:
    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True


def mark_task_result(
    task_id: str, status: str, error: str | None, next_run_at=None
) -> None:
    """记录任务执行结果（调度器调用）。"""
    from datetime import datetime, timezone

    with SessionLocal() as db:
        t = db.get(Task, task_id)
        if not t:
            return
        t.last_status = status
        t.last_error = error
        t.last_run_at = datetime.now(timezone.utc)
        t.next_run_at = next_run_at
        db.commit()
