"""
新增 invite_codes 邀请码数据表以支持封闭/邀请制/开放三种用户注册模式。

Revision ID: 0003_invite_codes
Revises: 0002_store_enrichment_and_auth
Create Date: 2026-11-24 22:48:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_invite_codes"
down_revision: str | Sequence[str] | None = "0002_store_enrichment_and_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    创建 invite_codes 邀请注册码表及唯一代码索引。
    """
    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("invite_codes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_invite_codes_code"), ["code"], unique=True)


def downgrade() -> None:
    """
    回滚删除 invite_codes 表。
    """
    with op.batch_alter_table("invite_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invite_codes_code"))
    op.drop_table("invite_codes")
