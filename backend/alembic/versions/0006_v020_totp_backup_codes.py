"""
为 users 表新增 TOTP 一次性备用恢复码哈希列表字段。

Revision ID: 0006_v020_totp_backup_codes
Revises: 0005_v020_user_prefs_and_passkeys
Create Date: 2026-11-27 18:25:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_v020_totp_backup_codes"
down_revision: str | Sequence[str] | None = "0005_v020_user_prefs_and_passkeys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    为 users 表新增 totp_backup_codes JSON 列（默认空数组）。
    """
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "totp_backup_codes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    """
    回滚移除 users.totp_backup_codes 列。
    """
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("totp_backup_codes")
