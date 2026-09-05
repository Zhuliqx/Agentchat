"""Alembic 环境：连接串与目标元数据取自应用配置/ORM，避免双份维护。"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Column, DateTime, String, Table, Text, engine_from_config, pool

# 允许在 backend 或项目根执行 alembic 时都能导入 app 包
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.db.models import Base  # noqa: E402

# 运行时设置表不是 ORM 模型，但属于应用 Schema：登记给 Alembic 做漂移对比
Table(
    "app_settings",
    Base.metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text(), nullable=False),
    extend_existing=True,
)
Table(
    "token_sessions",
    Base.metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    extend_existing=True,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.postgres_dsn)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to) -> bool:
    """只对比应用 Schema：忽略 LangGraph/向量库自动维护的表与外键/列。"""
    if type_ == "table":
        return name in target_metadata.tables
    # 列/索引/约束：模型新增或双方一致才纳入对比；库里多出的历史对象不自动删除
    return not reflected or compare_to is not None


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接 Postgres 执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
