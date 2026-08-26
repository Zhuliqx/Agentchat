"""SQLAlchemy ORM 模型。

Postgres 用于存储：
- 会话与对话历史（sessions / messages）
- 文档元数据（documents，向量在 Milvus）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def gen_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """平台用户（JWT 认证主体）。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    avatar_color: Mapped[str] = mapped_column(String(16), default="accent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    # 所属用户：未登录访客统一为 settings.guest_user_id（"default"，init_db 会创建该内置用户）
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        default="default",
        server_default="default",
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True
    )

    user: Mapped["User"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    # 引用溯源：assistant 消息的 RAG 检索命中来源（文档路径列表），持久化以便切换会话后回溯
    sources: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["Session"] = relationship(back_populates="messages")


class Document(Base):
    """文档元数据。向量存储在 Milvus，ID 与此表一致。

    user_id：知识库按用户隔离（每个用户拥有独立知识库）。
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "chunk_index", name="uq_user_source_chunk"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        String(64), default="default", index=True
    )  # 知识库归属用户
    filename: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(500), index=True)  # 文档来源标识
    tag: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)  # 文档标签（分组用）
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 整篇内容指纹（文档级去重）
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Task(Base):
    """定时/批处理任务。调度器后台扫描本表执行到期的任务。"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(100))
    # 任务类型，见 app/scheduler.py TASK_REGISTRY
    task_type: Mapped[str] = mapped_column(String(64))
    # 调度表达式：interval:<秒>（如 interval:3600）或 cron:<分钟级>（如 cron:*/30 每小时第30分）
    schedule: Mapped[str] = mapped_column(String(64), default="interval:3600")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # running | success | failed
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
