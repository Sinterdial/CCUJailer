"""
初始化 CCUJailer 核心数据表结构。

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-11-23 21:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    创建 ships、ccu_skus、changelog、webhook_rules、webhook_logs 与 system_settings 六张核心表。
    """
    op.create_table(
        "ships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("rsi_upgrade_id", sa.Integer(), nullable=True),
        sa.Column("rsi_matrix_id", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=160), nullable=False),
        sa.Column("name_zh", sa.String(length=160), nullable=False),
        sa.Column("auto_translated", sa.Boolean(), nullable=False),
        sa.Column("search_index", sa.String(length=512), nullable=False),
        sa.Column("manufacturer", sa.String(length=128), nullable=False),
        sa.Column("focus", sa.String(length=128), nullable=False),
        sa.Column("size", sa.String(length=64), nullable=False),
        sa.Column("production_status", sa.String(length=64), nullable=False),
        sa.Column("value_cents", sa.Integer(), nullable=False),
        sa.Column("always_available", sa.Boolean(), nullable=False),
        sa.Column("in_store", sa.Boolean(), nullable=False),
        sa.Column("store_url", sa.String(length=512), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ships", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ships_key"), ["key"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_ships_rsi_upgrade_id"), ["rsi_upgrade_id"], unique=True
        )
        batch_op.create_index(batch_op.f("ix_ships_search_index"), ["search_index"], unique=False)
        batch_op.create_index(batch_op.f("ix_ships_value_cents"), ["value_cents"], unique=False)

    op.create_table(
        "ccu_skus",
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("ship_id", sa.Integer(), nullable=False),
        sa.Column("rsi_upgrade_ship_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("edition_label", sa.String(length=120), nullable=False),
        sa.Column("is_warbond", sa.Boolean(), nullable=False),
        sa.Column("original_price_cents", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("store_url", sa.String(length=512), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listed_since_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ship_id"], ["ships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sku_id"),
    )
    with op.batch_alter_table("ccu_skus", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ccu_skus_is_warbond"), ["is_warbond"], unique=False)
        batch_op.create_index(batch_op.f("ix_ccu_skus_listed"), ["listed"], unique=False)
        batch_op.create_index(batch_op.f("ix_ccu_skus_price_cents"), ["price_cents"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ccu_skus_rsi_upgrade_ship_id"), ["rsi_upgrade_ship_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ccu_skus_ship_id"), ["ship_id"], unique=False)

    op.create_table(
        "changelog",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("ship_name_en", sa.String(length=160), nullable=False),
        sa.Column("ship_name_zh", sa.String(length=160), nullable=False),
        sa.Column("edition_label", sa.String(length=120), nullable=False),
        sa.Column("is_warbond", sa.Boolean(), nullable=False),
        sa.Column("old_price_cents", sa.Integer(), nullable=True),
        sa.Column("new_price_cents", sa.Integer(), nullable=True),
        sa.Column("original_price_cents", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("changelog", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_changelog_event_type"), ["event_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_changelog_occurred_at"), ["occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_changelog_sku_id"), ["sku_id"], unique=False)

    op.create_table(
        "webhook_rules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("custom_template", sa.Text(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webhook_rules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_webhook_rules_enabled"), ["enabled"], unique=False)

    op.create_table(
        "webhook_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("rule_name", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("ship_name", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["webhook_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webhook_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_webhook_logs_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_webhook_logs_rule_id"), ["rule_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_webhook_logs_success"), ["success"], unique=False)

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """
    删除核心业务数据表。
    """
    op.drop_table("system_settings")
    with op.batch_alter_table("webhook_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_webhook_logs_success"))
        batch_op.drop_index(batch_op.f("ix_webhook_logs_rule_id"))
        batch_op.drop_index(batch_op.f("ix_webhook_logs_created_at"))
    op.drop_table("webhook_logs")
    with op.batch_alter_table("webhook_rules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_webhook_rules_enabled"))
    op.drop_table("webhook_rules")
    with op.batch_alter_table("changelog", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_changelog_sku_id"))
        batch_op.drop_index(batch_op.f("ix_changelog_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_changelog_event_type"))
    op.drop_table("changelog")
    with op.batch_alter_table("ccu_skus", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ccu_skus_ship_id"))
        batch_op.drop_index(batch_op.f("ix_ccu_skus_rsi_upgrade_ship_id"))
        batch_op.drop_index(batch_op.f("ix_ccu_skus_price_cents"))
        batch_op.drop_index(batch_op.f("ix_ccu_skus_listed"))
        batch_op.drop_index(batch_op.f("ix_ccu_skus_is_warbond"))
    op.drop_table("ccu_skus")
    with op.batch_alter_table("ships", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ships_value_cents"))
        batch_op.drop_index(batch_op.f("ix_ships_search_index"))
        batch_op.drop_index(batch_op.f("ix_ships_rsi_upgrade_id"))
        batch_op.drop_index(batch_op.f("ix_ships_key"))
    op.drop_table("ships")
