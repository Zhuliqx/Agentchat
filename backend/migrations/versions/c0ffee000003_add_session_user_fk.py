"""为旧库 sessions.user_id 补齐外键（新库已由基线创建，跳过）。

Revision ID: c0ffee000003
Revises: a1b2c3d4e5f6
"""
from alembic import op
from sqlalchemy import inspect

revision: str = "c0ffee000003"
down_revision: str = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_FK_NAME = "fk_sessions_user_id_users"


def _has_user_fk(bind) -> bool:
    return any(
        fk.get("constrained_columns") == ["user_id"] and fk.get("referred_table") == "users"
        for fk in inspect(bind).get_foreign_keys("sessions")
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_user_fk(bind):
        op.create_foreign_key(
            _FK_NAME, "sessions", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_user_fk(bind):
        op.drop_constraint(_FK_NAME, "sessions", type_="foreignkey")
