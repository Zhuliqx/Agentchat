"""对齐旧库与 ORM：补 tag 索引、移除 ALTER TABLE 遗留的 server defaults。

Revision ID: a1b2c3d4e5f6
Revises: 96685ebfebbd
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "a1b2c3d4e5f6"
down_revision: str = "96685ebfebbd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "ix_documents_tag" not in {
        idx["name"] for idx in inspect(bind).get_indexes("documents")
    }:
        op.create_index(op.f("ix_documents_tag"), "documents", ["tag"], unique=False)
    # ORM 以 Python 默认值写入；旧手工 DDL 在列上留下的 server default 与模型不一致
    op.alter_column("documents", "user_id", server_default=None)
    op.alter_column("sessions", "pinned", server_default=None)
    op.alter_column("users", "avatar_color", server_default=None)


def downgrade() -> None:
    op.alter_column("users", "avatar_color", server_default="accent")
    op.alter_column("sessions", "pinned", server_default=sa.false())
    op.alter_column("documents", "user_id", server_default="default")
    bind = op.get_bind()
    if "ix_documents_tag" in {
        idx["name"] for idx in inspect(bind).get_indexes("documents")
    }:
        op.drop_index(op.f("ix_documents_tag"), table_name="documents")
