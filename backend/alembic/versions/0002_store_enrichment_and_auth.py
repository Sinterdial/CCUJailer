"""
扩展舰船全量商店/游戏内字段、新增单船商店 SKU 表、升级路径表及认证用户表。

Revision ID: 0002_store_enrichment_and_auth
Revises: 0001_initial_schema
Create Date: 2026-11-24 00:08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_store_enrichment_and_auth"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    升级 ships 表并创建 store_skus、upgrade_paths、users、oidc_states 四张新表。
    """
    # 1. 为现有 ships 表补充原版商店、武器、保险、价格历史与货舱网格字段
    with op.batch_alter_table("ships", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "manufacturer_code",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ship_type",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(sa.Column("min_crew", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("max_crew", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cargo_scu", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("length_m", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "loaners",
                sa.String(length=512),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "released_patch",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pledge_availability",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "gold_status",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "gold_patch",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "gold_family",
                sa.String(length=128),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "game_version",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "class_name",
                sa.String(length=128),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch_op.add_column(sa.Column("claim_time_min", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("expedite_time_min", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("expedite_cost_auec", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "ingame_buy",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ingame_rent",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("weapons_summary", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "price_history",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(sa.Column("cargo_grid", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "module_grids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    # 2. 创建单船商店版本与模块表
    op.create_table(
        "store_skus",
        sa.Column("sku_id", sa.String(length=64), nullable=False),
        sa.Column("ship_id", sa.Integer(), nullable=True),
        sa.Column("rsi_matrix_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("raw_name", sa.String(length=240), nullable=False),
        sa.Column("edition_label", sa.String(length=120), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("list_price_cents", sa.Integer(), nullable=True),
        sa.Column("is_warbond", sa.Boolean(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("limited", sa.Boolean(), nullable=False),
        sa.Column("stock_level", sa.String(length=64), nullable=False),
        sa.Column("insurance", sa.String(length=64), nullable=False),
        sa.Column("extras", sa.JSON(), nullable=False),
        sa.Column("store_url", sa.String(length=512), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("misses", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("listed_since_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ship_id"], ["ships.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("sku_id"),
    )
    with op.batch_alter_table("store_skus", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_store_skus_listed"), ["listed"], unique=False)
        batch_op.create_index(batch_op.f("ix_store_skus_ship_id"), ["ship_id"], unique=False)

    # 3. 创建升级路线规划图边表
    op.create_table(
        "upgrade_paths",
        sa.Column("from_upgrade_ship_id", sa.Integer(), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("from_upgrade_ship_id", "sku_id"),
    )

    # 4. 创建用户表与 OIDC PKCE 状态表
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("totp_pending_encrypted", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False),
        sa.Column("oidc_issuer", sa.String(length=512), nullable=True),
        sa.Column("oidc_sub", sa.String(length=255), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("oidc_issuer", "oidc_sub", name="uq_users_oidc_identity"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)

    op.create_table(
        "oidc_states",
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=256), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state"),
    )
    with op.batch_alter_table("oidc_states", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_oidc_states_expires_at"), ["expires_at"], unique=False)


def downgrade() -> None:
    """
    回滚新增认证表、商店 SKU 表及 ships 新增字段。
    """
    with op.batch_alter_table("oidc_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_oidc_states_expires_at"))
    op.drop_table("oidc_states")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
    op.drop_table("users")

    op.drop_table("upgrade_paths")

    with op.batch_alter_table("store_skus", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_store_skus_ship_id"))
        batch_op.drop_index(batch_op.f("ix_store_skus_listed"))
    op.drop_table("store_skus")

    with op.batch_alter_table("ships", schema=None) as batch_op:
        batch_op.drop_column("module_grids")
        batch_op.drop_column("cargo_grid")
        batch_op.drop_column("price_history")
        batch_op.drop_column("weapons_summary")
        batch_op.drop_column("ingame_rent")
        batch_op.drop_column("ingame_buy")
        batch_op.drop_column("expedite_cost_auec")
        batch_op.drop_column("expedite_time_min")
        batch_op.drop_column("claim_time_min")
        batch_op.drop_column("class_name")
        batch_op.drop_column("game_version")
        batch_op.drop_column("gold_family")
        batch_op.drop_column("gold_patch")
        batch_op.drop_column("gold_status")
        batch_op.drop_column("pledge_availability")
        batch_op.drop_column("released_patch")
        batch_op.drop_column("loaners")
        batch_op.drop_column("length_m")
        batch_op.drop_column("cargo_scu")
        batch_op.drop_column("max_crew")
        batch_op.drop_column("min_crew")
        batch_op.drop_column("ship_type")
        batch_op.drop_column("manufacturer_code")
