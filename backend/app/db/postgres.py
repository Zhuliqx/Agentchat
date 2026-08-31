"""Postgres 连接与会话管理。

使用 SQLAlchemy 2.x 同步引擎；对话历史读写集中在这里。
"""
from __future__ import annotations

from sqlalchemy import create_engine, func, select, text as _text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.models import Base, Document, Message, Session, Task, User, utcnow

engine = create_engine(
    settings.postgres_dsn,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """创建数据表（幂等），并对已存在的旧表做轻量迁移。"""
    Base.metadata.create_all(bind=engine)
    # 已存在的表不会自动补建索引/列，这里显式幂等创建（会话/文档排序查询加速 + 旧库迁移）
    with engine.begin() as conn:
        # 迁移：旧版 sessions 表无 user_id 列 → 补列并回填为 'default'
        conn.execute(
            _text(
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
                "user_id VARCHAR(64) NOT NULL DEFAULT 'default'"
            )
        )
        # 迁移：旧版 users 表无 avatar_color 列 → 补列（头像色板，默认 accent）
        conn.execute(
            _text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "avatar_color VARCHAR(16) NOT NULL DEFAULT 'accent'"
            )
        )
        # 迁移：旧版 sessions 表无 pinned 列 → 补列（会话置顶，默认 false）
        conn.execute(
            _text(
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
                "pinned BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        conn.execute(
            _text("UPDATE sessions SET user_id = 'default' WHERE user_id IS NULL")
        )
        # 迁移：旧版 documents 表无 user_id 列 → 补列并回填为 default（知识库按用户隔离）
        conn.execute(
            _text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                "user_id VARCHAR(64) NOT NULL DEFAULT 'default'"
            )
        )
        # 迁移：旧版 documents 表无 tag 列 → 补列（文档标签/分组）
        conn.execute(
            _text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                "tag VARCHAR(50)"
            )
        )
        # 迁移：旧版 documents 表无 content_hash 列 → 补列（文档级去重）
        conn.execute(
            _text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                "content_hash VARCHAR(64)"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_documents_content_hash "
                "ON documents (content_hash)"
            )
        )
        # 迁移：向量同步状态（Postgres 事实源 → Milvus 派生索引的对账标记）。
        # 存量行默认 synced（假定旧数据已入库），新摄入显式置 pending。
        conn.execute(
            _text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                "vector_status VARCHAR(16) NOT NULL DEFAULT 'synced'"
            )
        )
        conn.execute(
            _text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
                "vector_synced_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_documents_vector_status "
                "ON documents (vector_status)"
            )
        )
        # 迁移：documents 唯一约束升级为含 user_id（多用户可摄入同一路径文档）。
        # 旧库是 uq_source_chunk(source, chunk_index)；新模型是
        # uq_user_source_chunk(user_id, source, chunk_index)。先删旧约束，再幂等补新约束。
        conn.execute(
            _text("ALTER TABLE documents DROP CONSTRAINT IF EXISTS uq_source_chunk")
        )
        conn.execute(
            _text(
                "DO $$ BEGIN "
                "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                "'uq_user_source_chunk') THEN "
                "ALTER TABLE documents ADD CONSTRAINT uq_user_source_chunk "
                "UNIQUE (user_id, source, chunk_index); "
                "END IF; END $$;"
            )
        )
        # 迁移：旧版 messages 表无 sources 列 → 补列（引用溯源，JSON 数组）
        conn.execute(
            _text(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS sources JSON"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_documents_user_id "
                "ON documents (user_id)"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_sessions_updated_at "
                "ON sessions (updated_at DESC)"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_documents_created_at "
                "ON documents (created_at DESC)"
            )
        )
        conn.execute(
            _text(
                "CREATE INDEX IF NOT EXISTS ix_sessions_user_id "
                "ON sessions (user_id)"
            )
        )
    # 内置访客用户（guest_user_id）：保证 sessions.user_id 外键引用完整性。
    # 用随机密码哈希，访客无法登录该账号。
    _ensure_guest_user()


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


def list_sessions(user_id: str | None = None, limit: int = 50) -> list[Session]:
    with SessionLocal() as db:
        stmt = select(Session).order_by(
            Session.pinned.desc(), Session.updated_at.desc()
        )
        if user_id:
            stmt = stmt.where(Session.user_id == user_id)
        stmt = stmt.limit(limit)
        return list(db.scalars(stmt))


def rename_session(
    session_id: str, title: str | None = None, pinned: bool | None = None
) -> Session | None:
    with SessionLocal() as db:
        s = db.get(Session, session_id)
        if not s:
            return None
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
