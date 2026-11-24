"""
动态系统配置与周期清理服务模块。

管理 system_settings 表中的可热更新参数，提供强类型读取视图与过期历史日志清理能力。
所有动态配置键严格来源于 cj.core.config.SystemConfigKey。
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cj.core.config import (
    DEFAULT_DYNAMIC_SETTINGS,
    LanguageMode,
    RegistrationMode,
    Settings,
    SystemConfigKey,
    UserRole,
)
from cj.core.logging import get_logger
from cj.db.models import utc_now
from cj.repositories.changelog_repo import ChangelogRepository
from cj.repositories.setting_repo import SettingRepository
from cj.repositories.webhook_repo import WebhookRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DynamicSettingsSnapshot:
    """
    已解析的强类型动态系统配置快照。

    @ivar scrape_interval_seconds: CCU 与商店抓取周期（秒）
    @ivar catalog_sync_interval_seconds: Ship Matrix 与汉化词典同步周期（秒）
    @ivar i18n_source_url: 远程社区汉化词典源地址
    @ivar default_language: 前端默认船名显示模式（bilingual/zh/en）
    @ivar log_retention_days: 历史记录自动保留天数（0 表示永久保留）
    @ivar translate_enabled: 是否启用 OpenAI 兼容自动翻译兜底
    @ivar translate_api_base: 翻译接口 Base URL
    @ivar translate_model: 翻译接口模型名称
    @ivar registration_mode: 新用户注册模式（open/invite_only/closed）
    @ivar sso_enabled: 是否启用 OIDC SSO 登录
    @ivar sso_provider_name: SSO 提供方显示名称
    @ivar sso_issuer_url: OIDC Issuer 根地址
    @ivar sso_client_id: OIDC PKCE 公共客户端 Client ID
    @ivar sso_scopes: OIDC 授权 Scope 列表
    @ivar sso_auto_register: SSO 首次登录时是否自动创建账号
    @ivar sso_default_role: SSO 自动注册账号的默认角色
    @ivar upgrade_planner_enabled: 是否启用升级路线规划（/plan）功能
    """

    scrape_interval_seconds: int
    catalog_sync_interval_seconds: int
    i18n_source_url: str
    default_language: LanguageMode
    log_retention_days: int
    translate_enabled: bool
    translate_api_base: str
    translate_model: str
    registration_mode: RegistrationMode
    sso_enabled: bool
    sso_provider_name: str
    sso_issuer_url: str
    sso_client_id: str
    sso_scopes: str
    sso_auto_register: bool
    sso_default_role: UserRole
    upgrade_planner_enabled: bool


def _parse_int(raw: str | None, fallback: int, minimum: int, maximum: int) -> int:
    """
    将配置字符串安全解析为受边界约束的整数。

    @param raw: 原始字符串值
    @param fallback: 解析失败时的默认值
    @param minimum: 允许最小值
    @param maximum: 允许最大值
    @return: 合法整数值
    """
    try:
        val = int((raw or "").strip())
    except ValueError:
        return fallback
    return max(minimum, min(maximum, val))


def _parse_bool(raw: str | None, fallback: bool = False) -> bool:
    """
    将配置字符串解析为布尔值。

    @param raw: 原始字符串值
    @param fallback: 空值默认结果
    @return: 解析后的布尔值
    """
    if raw is None:
        return fallback
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_language(raw: str | None) -> LanguageMode:
    """
    校验并规范化前端默认语言显示模式。

    @param raw: 原始语言字符串
    @return: 合法的 LanguageMode 字面量
    """
    cleaned = (raw or "").strip().lower()
    if cleaned in ("bilingual", "zh", "en"):
        return cleaned  # type: ignore[return-value]
    return "bilingual"


def _parse_role(raw: str | None) -> UserRole:
    """
    校验并规范化角色字面量。

    @param raw: 原始角色字符串
    @return: 合法的 UserRole（admin 或 user）
    """
    cleaned = (raw or "").strip().lower()
    if cleaned in ("admin", "user"):
        return cleaned  # type: ignore[return-value]
    return "user"


def _parse_registration_mode(raw: str | None) -> RegistrationMode:
    """
    校验并规范化用户自助注册策略字面量。

    @param raw: 原始策略字符串
    @return: 合法的 RegistrationMode（open、invite_only 或 closed）
    """
    cleaned = (raw or "").strip().lower()
    if cleaned in ("open", "invite_only", "closed"):
        return cleaned  # type: ignore[return-value]
    return "closed"


class SettingService:
    """
    动态系统配置业务编排服务。

    @ivar _session_factory: 异步数据库会话工厂
    @ivar _setting_repo: 动态配置数据仓库
    @ivar _changelog_repo: 变动日志数据仓库
    @ivar _webhook_repo: Webhook 日志数据仓库
    @ivar _settings: 可选静态配置实例，用于提供环境变量默认值
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        setting_repo: SettingRepository,
        changelog_repo: ChangelogRepository,
        webhook_repo: WebhookRepository,
        settings: Settings | None = None,
    ) -> None:
        """
        初始化动态配置服务。

        @param session_factory: 异步会话工厂
        @param setting_repo: 系统配置 Repository
        @param changelog_repo: 变动日志 Repository
        @param webhook_repo: Webhook Repository
        @param settings: 可选的静态配置实例，提供认证与 SSO 环境变量默认值
        """
        self._session_factory = session_factory
        self._setting_repo = setting_repo
        self._changelog_repo = changelog_repo
        self._webhook_repo = webhook_repo
        self._settings = settings

    async def get_settings(self) -> DynamicSettingsSnapshot:
        """
        读取数据库当前动态配置并与静态环境变量默认值及系统默认值合并为强类型快照。

        @return: DynamicSettingsSnapshot 实例
        """
        async with self._session_factory() as session:
            stored = await self._setting_repo.get_all_map(session)

        merged = {k.value: v for k, v in DEFAULT_DYNAMIC_SETTINGS.items()}
        if self._settings is not None:
            merged[SystemConfigKey.REGISTRATION_MODE.value] = (
                self._settings.auth_registration_mode
            )
            merged[SystemConfigKey.SSO_ENABLED.value] = (
                "true" if self._settings.oidc_enabled else "false"
            )
            if self._settings.oidc_provider_name.strip():
                merged[SystemConfigKey.SSO_PROVIDER_NAME.value] = (
                    self._settings.oidc_provider_name.strip()
                )
            if self._settings.oidc_issuer_url.strip():
                merged[SystemConfigKey.SSO_ISSUER_URL.value] = (
                    self._settings.oidc_issuer_url.strip()
                )
            if self._settings.oidc_client_id.strip():
                merged[SystemConfigKey.SSO_CLIENT_ID.value] = self._settings.oidc_client_id.strip()
            if self._settings.oidc_scopes.strip():
                merged[SystemConfigKey.SSO_SCOPES.value] = self._settings.oidc_scopes.strip()
            merged[SystemConfigKey.SSO_AUTO_REGISTER.value] = (
                "true" if self._settings.oidc_auto_register else "false"
            )
            merged[SystemConfigKey.SSO_DEFAULT_ROLE.value] = self._settings.oidc_default_role
            merged[SystemConfigKey.UPGRADE_PLANNER_ENABLED.value] = (
                "true" if self._settings.upgrade_planner_enabled else "false"
            )
        merged.update(stored)

        return DynamicSettingsSnapshot(
            scrape_interval_seconds=_parse_int(
                merged.get(SystemConfigKey.SCRAPE_INTERVAL_SECONDS), 300, 30, 86400
            ),
            catalog_sync_interval_seconds=_parse_int(
                merged.get(SystemConfigKey.CATALOG_SYNC_INTERVAL_SECONDS), 86400, 300, 604800
            ),
            i18n_source_url=(merged.get(SystemConfigKey.I18N_SOURCE_URL) or "").strip(),
            default_language=_parse_language(merged.get(SystemConfigKey.DEFAULT_LANGUAGE)),
            log_retention_days=_parse_int(
                merged.get(SystemConfigKey.LOG_RETENTION_DAYS), 30, 0, 3650
            ),
            translate_enabled=_parse_bool(merged.get(SystemConfigKey.TRANSLATE_ENABLED), False),
            translate_api_base=(
                merged.get(SystemConfigKey.TRANSLATE_API_BASE)
                or DEFAULT_DYNAMIC_SETTINGS[SystemConfigKey.TRANSLATE_API_BASE]
            ).strip(),
            translate_model=(
                merged.get(SystemConfigKey.TRANSLATE_MODEL)
                or DEFAULT_DYNAMIC_SETTINGS[SystemConfigKey.TRANSLATE_MODEL]
            ).strip(),
            registration_mode=_parse_registration_mode(
                merged.get(SystemConfigKey.REGISTRATION_MODE)
            ),
            sso_enabled=_parse_bool(merged.get(SystemConfigKey.SSO_ENABLED), False),
            sso_provider_name=(
                merged.get(SystemConfigKey.SSO_PROVIDER_NAME)
                or DEFAULT_DYNAMIC_SETTINGS[SystemConfigKey.SSO_PROVIDER_NAME]
            ).strip(),
            sso_issuer_url=(merged.get(SystemConfigKey.SSO_ISSUER_URL) or "").strip().rstrip("/"),
            sso_client_id=(merged.get(SystemConfigKey.SSO_CLIENT_ID) or "").strip(),
            sso_scopes=(
                merged.get(SystemConfigKey.SSO_SCOPES)
                or DEFAULT_DYNAMIC_SETTINGS[SystemConfigKey.SSO_SCOPES]
            ).strip(),
            sso_auto_register=_parse_bool(merged.get(SystemConfigKey.SSO_AUTO_REGISTER), True),
            sso_default_role=_parse_role(merged.get(SystemConfigKey.SSO_DEFAULT_ROLE)),
            upgrade_planner_enabled=_parse_bool(
                merged.get(SystemConfigKey.UPGRADE_PLANNER_ENABLED), False
            ),
        )

    async def update_settings(self, updates: dict[SystemConfigKey, str]) -> DynamicSettingsSnapshot:
        """
        在显式事务中热更新一项或多项动态配置并返回更新后的完整快照。

        @param updates: 以 SystemConfigKey 为键的配置变更字典
        @return: 更新后的 DynamicSettingsSnapshot 实例
        """
        serialized = {key.value: str(val).strip() for key, val in updates.items()}
        async with self._session_factory() as session:
            async with session.begin():
                await self._setting_repo.upsert_many(session, serialized)

        logger.info("Updated %d dynamic system setting(s): %s", len(serialized), list(serialized))
        return await self.get_settings()

    async def get_scrape_interval(self) -> int:
        """
        快捷获取当前生效的 CCU 抓取间隔秒数（供调度器回调读取）。

        @return: 抓取间隔秒数
        """
        snapshot = await self.get_settings()
        return snapshot.scrape_interval_seconds

    async def get_catalog_sync_interval(self) -> int:
        """
        快捷获取当前生效的目录与汉化同步间隔秒数（供调度器回调读取）。

        @return: 目录同步间隔秒数
        """
        snapshot = await self.get_settings()
        return snapshot.catalog_sync_interval_seconds

    async def cleanup_expired_logs(self) -> tuple[int, int]:
        """
        按配置的日志保留天数自动清理过期 webhook_logs 与 changelog 记录。

        当 LOG_RETENTION_DAYS 设为 0 时跳过清理。

        @return: (删除的 webhook_logs 条数, 删除的 changelog 条数)
        """
        snapshot = await self.get_settings()
        if snapshot.log_retention_days <= 0:
            return 0, 0

        cutoff = utc_now() - timedelta(days=snapshot.log_retention_days)
        async with self._session_factory() as session:
            async with session.begin():
                deleted_wh = await self._webhook_repo.delete_logs_older_than(session, cutoff)
                deleted_cl = await self._changelog_repo.delete_older_than(session, cutoff)

        if deleted_wh or deleted_cl:
            logger.info(
                "Cleaned expired logs (retention=%dd): webhook_logs=%d, changelog=%d",
                snapshot.log_retention_days,
                deleted_wh,
                deleted_cl,
            )
        return deleted_wh, deleted_cl
