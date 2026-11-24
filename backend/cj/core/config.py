"""
系统静态与动态配置定义模块。

静态配置基于 pydantic-settings 从环境变量及 .env 文件加载并在启动时强校验；
动态配置键通过 SystemConfigKey 枚举集中管理，禁止在业务代码中出现魔法字符串。
"""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

LanguageMode = Literal["bilingual", "zh", "en"]
UserRole = Literal["admin", "user"]
RegistrationMode = Literal["open", "invite_only", "closed"]

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class SystemConfigKey(StrEnum):
    """
    动态系统配置键枚举。

    对应数据库 system_settings 表的 key 字段，支持运行时通过接口热更新。
    """

    SCRAPE_INTERVAL_SECONDS = "scrape_interval_seconds"
    CATALOG_SYNC_INTERVAL_SECONDS = "catalog_sync_interval_seconds"
    I18N_SOURCE_URL = "i18n_source_url"
    DEFAULT_LANGUAGE = "default_language"
    LOG_RETENTION_DAYS = "log_retention_days"
    TRANSLATE_ENABLED = "translate_enabled"
    TRANSLATE_API_BASE = "translate_api_base"
    TRANSLATE_MODEL = "translate_model"
    REGISTRATION_MODE = "registration_mode"
    SSO_ENABLED = "sso_enabled"
    SSO_PROVIDER_NAME = "sso_provider_name"
    SSO_ISSUER_URL = "sso_issuer_url"
    SSO_CLIENT_ID = "sso_client_id"
    SSO_SCOPES = "sso_scopes"
    SSO_AUTO_REGISTER = "sso_auto_register"
    SSO_DEFAULT_ROLE = "sso_default_role"
    UPGRADE_PLANNER_ENABLED = "upgrade_planner_enabled"


DEFAULT_DYNAMIC_SETTINGS: dict[SystemConfigKey, str] = {
    SystemConfigKey.SCRAPE_INTERVAL_SECONDS: "300",
    SystemConfigKey.CATALOG_SYNC_INTERVAL_SECONDS: "86400",
    SystemConfigKey.I18N_SOURCE_URL: "",
    SystemConfigKey.DEFAULT_LANGUAGE: "bilingual",
    SystemConfigKey.LOG_RETENTION_DAYS: "30",
    SystemConfigKey.TRANSLATE_ENABLED: "false",
    SystemConfigKey.TRANSLATE_API_BASE: "https://api.openai.com/v1",
    SystemConfigKey.TRANSLATE_MODEL: "gpt-4o-mini",
    SystemConfigKey.REGISTRATION_MODE: "closed",
    SystemConfigKey.SSO_ENABLED: "false",
    SystemConfigKey.SSO_PROVIDER_NAME: "ZITADEL",
    SystemConfigKey.SSO_ISSUER_URL: "",
    SystemConfigKey.SSO_CLIENT_ID: "",
    SystemConfigKey.SSO_SCOPES: "openid profile email",
    SystemConfigKey.SSO_AUTO_REGISTER: "true",
    SystemConfigKey.SSO_DEFAULT_ROLE: "user",
    SystemConfigKey.UPGRADE_PLANNER_ENABLED: "false",
}


class Settings(BaseSettings):
    """
    CCUJailer 应用静态配置模型。

    在进程启动阶段加载并验证关键基础设施参数（数据库、网络代理、端口、鉴权与日志策略）。
    本地开发时项目 .env 文件优先级高于宿主机残留环境变量，避免系统旧代理覆盖本地配置。

    @cvar model_config: Pydantic Settings 配置元信息，支持读取根/后端 .env 与环境变量
    """

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/ccujailer.db",
        description="异步数据库连接字符串（支持 sqlite+aiosqlite:// 与 postgresql+asyncpg://）",
    )
    data_dir: Path = Field(
        default=Path("./data"),
        description="本地持久化数据目录（存放 SQLite 库、备份、覆盖词典与日志）",
    )
    host: str = Field(default="0.0.0.0", description="HTTP 服务监听地址")
    port: int = Field(default=8794, description="HTTP 服务监听端口")
    auto_migrate: bool = Field(
        default=True,
        description="启动前是否自动执行 Alembic 迁移",
    )
    http_proxy: str | None = Field(default=None, description="HTTP 出站代理地址")
    https_proxy: str | None = Field(default=None, description="HTTPS 出站代理地址")
    all_proxy: str | None = Field(default=None, description="全局通用代理地址（支持 SOCKS5）")
    no_proxy: str = Field(
        default="localhost,127.0.0.1",
        description="绕过代理的主机列表，逗号分隔",
    )
    webhook_use_proxy: bool = Field(
        default=False,
        description="发送 Webhook 推送时是否使用容器出站代理",
    )
    admin_token: str = Field(
        default="",
        description="可选的脚本向后兼容管理访问令牌；保留为空时仅使用用户账号/SSO认证",
    )
    auth_require_login: bool = Field(
        default=True,
        description=(
            "是否强制全站登录访问（仅通过环境变量 AUTH_REQUIRE_LOGIN 配置，Web UI 不可修改）"
        ),
    )
    auth_registration_mode: RegistrationMode = Field(
        default="closed",
        description="默认用户注册策略（open=开放注册 / invite_only=仅限邀请 / closed=关闭注册）",
    )
    auth_secret_key: str = Field(
        default="",
        description="签名 JWT 与加密存储 OTP 密钥的主密钥；留空则在 data_dir 自动持久化生成",
    )
    auth_token_expire_hours: int = Field(
        default=168,
        ge=1,
        le=8760,
        description="登录会话 JWT 有效期（小时），默认 168 小时（7 天）",
    )
    init_admin_username: str = Field(
        default="",
        description="首次启动无用户时自动创建的初始管理员用户名",
    )
    init_admin_password: str = Field(
        default="",
        description="首次启动无用户时自动创建的初始管理员密码",
    )
    oidc_enabled: bool = Field(
        default=False,
        description="SSO 默认启用状态（可被系统设置热更新覆盖）",
    )
    oidc_provider_name: str = Field(
        default="ZITADEL",
        description="SSO 提供方显示名称默认值",
    )
    oidc_issuer_url: str = Field(
        default="",
        description="OIDC Issuer 根地址默认值（例如 ZITADEL 实例域名）",
    )
    oidc_client_id: str = Field(
        default="",
        description="OIDC PKCE 公共客户端 Client ID 默认值（无需 Client Secret）",
    )
    oidc_scopes: str = Field(
        default="openid profile email",
        description="OIDC 授权请求 Scope 默认列表",
    )
    oidc_auto_register: bool = Field(
        default=True,
        description="首次通过 SSO 登录时是否自动创建账号默认值",
    )
    oidc_default_role: UserRole = Field(
        default="user",
        description="SSO 自动注册账号默认角色（user/admin）",
    )
    upgrade_planner_enabled: bool = Field(
        default=False,
        description="是否默认启用升级路线规划（/plan）功能",
    )
    translate_api_key: str = Field(
        default="",
        description="可选的 OpenAI 兼容翻译兜底接口密钥",
    )
    log_level: str = Field(default="INFO", description="全局日志级别")
    log_to_file: bool = Field(
        default=False,
        description="是否同时写入本地轮转日志文件",
    )
    frontend_dist_dir: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "frontend" / "dist",
        description="前端静态构建产物目录",
    )
    enable_scheduler: bool = Field(
        default=True,
        description="是否启用后台定时抓取与清理调度器",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        自定义配置源优先级：显式入参 > .env 文件 > 系统环境变量 > Secrets 文件。

        确保本地调试时项目 .env 中的代理与端口等配置不被宿主机既有环境变量覆盖。

        @param settings_cls: Settings 配置类
        @param init_settings: 代码构造传参源
        @param env_settings: 进程环境变量源
        @param dotenv_settings: .env 文件源
        @param file_secret_settings: 密钥文件源
        @return: 按优先级降序排列的配置源元组
        """
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @field_validator("http_proxy", "https_proxy", "all_proxy", mode="before")
    @classmethod
    def _normalize_proxy_url(cls, value: Any) -> str | None:
        """
        清理代理地址空白并自动为缺少协议头的地址补全 http:// 前缀。

        @param value: 原始代理地址字符串或 None
        @return: 规范化后的代理 URL，空值返回 None
        """
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if "://" not in cleaned:
            cleaned = f"http://{cleaned}"
        return cleaned

    @field_validator("frontend_dist_dir")
    @classmethod
    def _resolve_frontend_dist_dir(cls, value: Path) -> Path:
        """
        解析前端构建目录；若相对路径在当前工作目录下不存在，则回退按 backend 目录解析。

        @param value: 原始前端产物目录路径
        @return: 解析后的前端产物目录路径
        """
        if value.is_absolute() or value.exists():
            return value
        backend_relative = (BACKEND_DIR / value).resolve()
        if backend_relative.exists():
            return backend_relative
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        """
        校验数据库连接字符串驱动前缀是否受支持。

        @param value: 原始数据库连接字符串
        @return: 校验通过的数据库连接字符串
        @raise ValueError: 当驱动前缀不受支持时抛出
        """
        cleaned = value.strip()
        supported_prefixes = ("sqlite+aiosqlite://", "postgresql+asyncpg://")
        if not cleaned.startswith(supported_prefixes):
            raise ValueError(
                "DATABASE_URL must start with 'sqlite+aiosqlite://' or 'postgresql+asyncpg://'"
            )
        return cleaned

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        """
        校验服务监听端口范围。

        @param value: 端口号
        @return: 合法端口号
        @raise ValueError: 当端口号不在 1-65535 之间时抛出
        """
        if not 1 <= value <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """
        校验日志级别合法性并规范化为大写。

        @param value: 日志级别文本
        @return: 规范化后的大写日志级别名称
        @raise ValueError: 当日志级别不被允许时抛出
        """
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return level

    @property
    def is_sqlite(self) -> bool:
        """
        判断当前配置是否使用 SQLite 引擎。

        @return: 若为 SQLite 返回 True，否则返回 False
        """
        return self.database_url.startswith("sqlite+aiosqlite://")

    @property
    def is_postgres(self) -> bool:
        """
        判断当前配置是否使用 PostgreSQL 引擎。

        @return: 若为 PostgreSQL 返回 True，否则返回 False
        """
        return self.database_url.startswith("postgresql+asyncpg://")

    def resolve_sqlite_path(self) -> Path | None:
        """
        从 SQLite 连接串解析本地数据库文件路径。

        @return: SQLite 文件对应的 Path 对象；非文件型 SQLite 或 PostgreSQL 返回 None
        """
        if not self.is_sqlite:
            return None
        prefix = "sqlite+aiosqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        raw_path = self.database_url[len(prefix) :].split("?", 1)[0]
        if raw_path == ":memory:":
            return None
        return Path(raw_path)
