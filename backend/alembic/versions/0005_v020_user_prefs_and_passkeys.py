"""
新增用户跨设备偏好设置字段、Passkey 通行密钥表与默认 StarCitizenToolBox 汉化源升级迁移。

Revision ID: 0005_v020_user_prefs_and_passkeys
Revises: 0004_cleanup_abnormal_changelogs
Create Date: 2026-11-27 00:38:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from cj.core.config import DEFAULT_SCTOOLBOX_I18N_URL

revision: str = "0005_v020_user_prefs_and_passkeys"
down_revision: str | Sequence[str] | None = "0004_cleanup_abnormal_changelogs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    扩展 users.preferences 列，创建 user_passkeys 与 webauthn_challenges 表，
    并在 i18n_source_url 为空时自动切换至 StarCitizenToolBox 默认汉化源。
    """
    # 1. 为 users 表新增跨设备个人偏好 JSON 字段
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "preferences",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    # 2. 创建用户 Passkey 凭据表
    op.create_table(
        "user_passkeys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=512), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=120), nullable=False, server_default="Passkey"),
        sa.Column("transports", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("backed_up", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("aaguid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_passkeys", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_passkeys_user_id"), ["user_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_user_passkeys_credential_id"), ["credential_id"], unique=True
        )

    # 3. 创建 WebAuthn 临时挑战状态表
    op.create_table(
        "webauthn_challenges",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("challenge", sa.String(length=256), nullable=False),
        sa.Column("rp_id", sa.String(length=255), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webauthn_challenges", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_webauthn_challenges_expires_at"), ["expires_at"], unique=False
        )

    # 4. 若系统设置中 i18n_source_url 已存在但仍为空串，则升级填充默认 StarCitizenToolBox 汉化源
    settings_table = sa.table(
        "system_settings",
        sa.column("key", sa.String()),
        sa.column("value", sa.Text()),
    )
    op.execute(
        sa.update(settings_table)
        .where(settings_table.c.key == "i18n_source_url", settings_table.c.value == "")
        .values(value=DEFAULT_SCTOOLBOX_I18N_URL)
    )


def downgrade() -> None:
    """
    回滚删除 webauthn_challenges、user_passkeys 表并移除 users.preferences 列。
    """
    with op.batch_alter_table("webauthn_challenges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_webauthn_challenges_expires_at"))
    op.drop_table("webauthn_challenges")

    with op.batch_alter_table("user_passkeys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_passkeys_credential_id"))
        batch_op.drop_index(batch_op.f("ix_user_passkeys_user_id"))
    op.drop_table("user_passkeys")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("preferences")
