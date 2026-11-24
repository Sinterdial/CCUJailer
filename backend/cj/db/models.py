"""
SQLAlchemy 2.0 异步 ORM 数据模型定义。

所有时间字段统一使用带 UTC 时区的 DateTime(timezone=True)，
所有结构化扩展字段统一使用跨方言 JSON 类型，严禁引入特定数据库的私有列类型。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """
    获取当前 UTC 时区感知时间。

    @return: 带 UTC 时区的当前时间对象
    """
    return datetime.now(UTC)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    确保从 SQLite 或外部读取的时间对象均带有 UTC 时区信息。

    @param dt: 待规范化的时间对象或 None
    @return: 带 UTC 时区的时间对象或 None
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class Base(DeclarativeBase):
    """
    ORM 模型声明式基类。
    """


class Ship(Base):
    """
    星际公民全量舰船主表。

    聚合来自 RSI Ship Matrix、在售单船商店与升级（CCU）工具的舰船元数据与中英双语检索索引。

    @cvar __tablename__: 数据库表名 ships
    """

    __tablename__ = "ships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    rsi_upgrade_id: Mapped[int | None] = mapped_column(
        Integer, unique=True, index=True, nullable=True
    )
    rsi_matrix_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    auto_translated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    search_index: Mapped[str] = mapped_column(String(512), index=True, default="", nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    manufacturer_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    focus: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    ship_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    size: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    min_crew: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_crew: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cargo_scu: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    production_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    loaners: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    released_patch: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    pledge_availability: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gold_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gold_patch: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gold_family: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    game_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    class_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    claim_time_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    expedite_time_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    expedite_cost_auec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingame_buy: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    ingame_rent: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    weapons_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    price_history: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    cargo_grid: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    module_grids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    value_cents: Mapped[int] = mapped_column(Integer, index=True, default=0, nullable=False)
    always_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_store: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    store_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    image_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CcuSku(Base):
    """
    RSI 升级（CCU）SKU 快照表。

    记录每个可购升级版本的当前价格、原价、Warbond 标识、在售状态、连续缺失次数与到期时间。

    @cvar __tablename__: 数据库表名 ccu_skus
    """

    __tablename__ = "ccu_skus"

    sku_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ship_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ships.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rsi_upgrade_ship_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    edition_label: Mapped[str] = mapped_column(String(120), default="Standard", nullable=False)
    is_warbond: Mapped[bool] = mapped_column(Boolean, index=True, default=False, nullable=False)
    original_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    listed: Mapped[bool] = mapped_column(Boolean, index=True, default=True, nullable=False)
    misses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    store_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    listed_since_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Changelog(Base):
    """
    CCU 上架、返场、调价与下架历史记录表。

    @cvar __tablename__: 数据库表名 changelog
    """

    __tablename__ = "changelog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sku_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ship_name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    ship_name_zh: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    edition_label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_warbond: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    old_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WebhookRule(Base):
    """
    可编程 Webhook 通知规则表。

    支持定义目标地址、平台消息格式模板、自定义请求头以及多维度 CCU 过滤条件。

    @cvar __tablename__: 数据库表名 webhook_rules
    """

    __tablename__ = "webhook_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=True, nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    method: Mapped[str] = mapped_column(String(16), default="POST", nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), default="generic", nullable=False)
    custom_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    headers: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    events: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class WebhookLog(Base):
    """
    Webhook 投递执行日志表。

    记录每次推送的 HTTP 状态码、重试次数、响应耗时及脱敏后的失败原因。

    @cvar __tablename__: 数据库表名 webhook_logs
    """

    __tablename__ = "webhook_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("webhook_rules.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule_name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ship_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, index=True, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )


class SystemSetting(Base):
    """
    动态系统配置键值表。

    键名严格与 cj.core.config.SystemConfigKey 枚举对应。

    @cvar __tablename__: 数据库表名 system_settings
    """

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class StoreSku(Base):
    """
    RSI 单船商店在售版本（Standard / Warbond / 10 Year / Best in Show）与单卖模块快照表。

    @cvar __tablename__: 数据库表名 store_skus
    """

    __tablename__ = "store_skus"

    sku_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ship_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ships.id", ondelete="SET NULL"), index=True, nullable=True
    )
    rsi_matrix_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="ship", nullable=False)
    raw_name: Mapped[str] = mapped_column(String(240), nullable=False)
    edition_label: Mapped[str] = mapped_column(String(120), default="Standard", nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    list_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_warbond: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    limited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stock_level: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    insurance: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    extras: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    store_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    image_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    listed: Mapped[bool] = mapped_column(Boolean, index=True, default=True, nullable=False)
    misses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    listed_since_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class UpgradePath(Base):
    """
    升级路线规划器可达边缓存表（记录每艘起始船可实际购入的目标 CCU SKU）。

    @cvar __tablename__: 数据库表名 upgrade_paths
    """

    __tablename__ = "upgrade_paths"

    from_upgrade_ship_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class User(Base):
    """
    系统账号与认证凭据表。

    支持本地账密（Argon2id）、TOTP 二步验证（Fernet 密文存储）与 OIDC SSO 身份绑定。

    @cvar __tablename__: 数据库表名 users
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_sub", name="uq_users_oidc_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_pending_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    oidc_issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class OidcState(Base):
    """
    OIDC Authorization Code + PKCE 短期临时状态表（防止 CSRF 与重放攻击）。

    @cvar __tablename__: 数据库表名 oidc_states
    """

    __tablename__ = "oidc_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(256), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class InviteCode(Base):
    """
    用户邀请注册凭证表。

    支持管理员生成限次/限时邀请码，用于「仅限邀请制注册」模式校验并分配初始角色。

    @cvar __tablename__: 数据库表名 invite_codes
    """

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
