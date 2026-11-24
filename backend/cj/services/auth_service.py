"""
用户认证、TOTP 二步验证、ZITADEL OIDC PKCE 无密钥 SSO 与用户管理业务服务。

所有密码采用 Argon2id 哈希存储，TOTP 密钥经 Fernet 对称加密落库，会话凭据采用签名 JWT
并通过 token_version 支持改密/禁用即时吊销；全部 OIDC HTTP 请求统一经由 NetworkManager 发出。
"""

import base64
import hashlib
import io
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
import pyotp
import segno
from jwt import InvalidTokenError, PyJWKSet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cj import APP_VERSION, GITHUB_REPO_URL
from cj.core.config import RegistrationMode, Settings, UserRole
from cj.core.logging import get_logger
from cj.core.net import NetworkManager
from cj.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    resolve_or_create_secret_key,
    verify_password,
)
from cj.db.models import InviteCode, OidcState, User, ensure_utc, utc_now
from cj.repositories.user_repo import UserRepository
from cj.services.setting_service import SettingService

logger = get_logger(__name__)

_USERNAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_ALLOWED_ID_TOKEN_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"]


class AuthError(Exception):
    """
    认证与用户管理领域异常（由接口层转换为对应 HTTP 状态码）。

    @ivar code: 标准化错误标识码
    @ivar message: 面向用户的错误描述文本
    """

    def __init__(self, code: str, message: str) -> None:
        """
        构造领域认证异常。

        @param code: 机器可读错误码
        @param message: 可展示中文错误信息
        """
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """
    已通过认证的当前请求主体上下文对象（不暴露 ORM 实例至 API 层）。

    @ivar id: 用户主键 ID（仅当使用静态 ADMIN_TOKEN 后备兼容方式时为 0）
    @ivar username: 用户名
    @ivar display_name: 显示名称
    @ivar email: 电子邮箱
    @ivar role: 角色名称（admin 或 user）
    @ivar is_active: 是否启用
    @ivar totp_enabled: 是否已开启 OTP 二步验证
    @ivar has_password: 是否设置了本地密码
    @ivar oidc_bound: 是否已绑定 OIDC SSO 外部身份
    """

    id: int
    username: str
    display_name: str
    email: str
    role: UserRole
    is_active: bool
    totp_enabled: bool
    has_password: bool
    oidc_bound: bool


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """
    登录认证结果数据对象。

    @ivar mfa_required: 是否需要继续完成 TOTP 二步验证
    @ivar mfa_token: 待完成 OTP 校验的短期挑战令牌
    @ivar access_token: 正式会话 JWT 字符串
    @ivar expires_at: 正式会话过期 UTC 时间
    @ivar user: 登录用户摘要对象
    """

    mfa_required: bool
    mfa_token: str | None = None
    access_token: str | None = None
    expires_at: datetime | None = None
    user: AuthenticatedUser | None = None


def _user_to_principal(user: User) -> AuthenticatedUser:
    """
    将数据库 User ORM 实体转换为无状态 AuthenticatedUser 值对象。

    @param user: User ORM 实体
    @return: AuthenticatedUser 实例
    """
    role: UserRole = "admin" if user.role == "admin" else "user"
    return AuthenticatedUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        email=user.email,
        role=role,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        has_password=bool(user.password_hash),
        oidc_bound=bool(user.oidc_issuer and user.oidc_sub),
    )


def _user_to_summary(user: User) -> dict[str, Any]:
    """
    将 User ORM 实体序列化为用户管理响应字典。

    @param user: User ORM 实体
    @return: 不包含任何哈希与密钥的公开用户摘要字典
    """
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "email": user.email,
        "role": "admin" if user.role == "admin" else "user",
        "is_active": user.is_active,
        "totp_enabled": user.totp_enabled,
        "has_password": bool(user.password_hash),
        "oidc_bound": bool(user.oidc_issuer and user.oidc_sub),
        "last_login_at": ensure_utc(user.last_login_at),
        "created_at": ensure_utc(user.created_at),
        "updated_at": ensure_utc(user.updated_at),
    }


def _invite_to_summary(invite: InviteCode) -> dict[str, Any]:
    """
    将 InviteCode ORM 实体序列化为后台邀请码列表响应字典。

    @param invite: InviteCode ORM 实体
    @return: 邀请码摘要字典
    """
    return {
        "id": invite.id,
        "code": invite.code,
        "role": "admin" if invite.role == "admin" else "user",
        "note": invite.note,
        "max_uses": invite.max_uses,
        "used_count": invite.used_count,
        "is_active": invite.is_active,
        "expires_at": ensure_utc(invite.expires_at),
        "created_at": ensure_utc(invite.created_at),
        "last_used_at": ensure_utc(invite.last_used_at),
    }


def _build_pkce_challenge(verifier: str) -> str:
    """
    按照 RFC 7636 S256 规范从 code_verifier 计算 PKCE code_challenge。

    @param verifier: 随机 PKCE code_verifier 明文
    @return: 无填充 URL-Safe Base64 编码的 SHA-256 摘要
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _render_totp_qr_svg(provisioning_uri: str) -> str:
    """
    将 otpauth:// 配对 URI 纯本地渲染为内联 SVG 二维码，不向任何外部图床请求数据。

    @param provisioning_uri: TOTP 配对 URI
    @return: UTF-8 SVG XML 文本
    """
    qr = segno.make(provisioning_uri, error="M")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", xmldecl=False, scale=5, border=2, dark="#1b1915", light="#ffffff")
    return buf.getvalue().decode("utf-8")


class AuthService:
    """
    系统认证、双因素 OTP、ZITADEL OIDC PKCE SSO 及用户权限管理服务。

    @ivar _settings: 应用静态配置
    @ivar _session_factory: 异步会话工厂
    @ivar _user_repo: 用户与 OIDC 状态仓库
    @ivar _setting_service: 动态系统配置服务
    @ivar _net: 统一出口网络管理器
    @ivar _secret_key: 运行期主密钥（签名 JWT 与加解密 TOTP 密钥）
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        user_repo: UserRepository,
        setting_service: SettingService,
        net: NetworkManager,
    ) -> None:
        """
        初始化认证业务服务并加载或持久化主密钥。

        @param settings: 应用静态配置实例
        @param session_factory: 异步会话工厂
        @param user_repo: 用户 Repository
        @param setting_service: 动态设置服务
        @param net: 统一网络管理器
        """
        self._settings = settings
        self._session_factory = session_factory
        self._user_repo = user_repo
        self._setting_service = setting_service
        self._net = net
        self._secret_key = resolve_or_create_secret_key(
            settings.auth_secret_key, settings.data_dir
        )

    def _issue_session_token(self, user: User) -> tuple[str, datetime]:
        """
        为已认证用户签发正式会话 JWT。

        @param user: 已验证通过的 User 实体
        @return: (JWT 字符串, 过期 UTC 时间) 二元组
        """
        now = utc_now()
        exp = now + timedelta(hours=self._settings.auth_token_expire_hours)
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "ver": user.token_version,
            "purpose": "session",
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        token = jwt.encode(payload, self._secret_key, algorithm="HS256")
        return token, exp

    def _issue_mfa_token(self, user: User) -> str:
        """
        签发有效期 5 分钟的 TOTP 二次验证挑战令牌（不可用于直接访问业务接口）。

        @param user: 通过账密校验的 User 实体
        @return: 短期挑战 JWT 字符串
        """
        now = utc_now()
        exp = now + timedelta(minutes=5)
        payload = {
            "sub": str(user.id),
            "ver": user.token_version,
            "purpose": "mfa_challenge",
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        return jwt.encode(payload, self._secret_key, algorithm="HS256")

    async def ensure_bootstrap_admin(self) -> None:
        """
        启动时检查账号表是否为空；若配置了 INIT_ADMIN_USERNAME 与 INIT_ADMIN_PASSWORD 则自动建号。
        """
        init_user = self._settings.init_admin_username.strip()
        init_pass = self._settings.init_admin_password
        if not init_user or not init_pass:
            return

        async with self._session_factory() as session:
            async with session.begin():
                if await self._user_repo.count_users(session) > 0:
                    return
                now = utc_now()
                admin = User(
                    username=init_user,
                    display_name=init_user,
                    email="",
                    password_hash=hash_password(init_pass),
                    role="admin",
                    is_active=True,
                    token_version=1,
                    created_at=now,
                    updated_at=now,
                )
                await self._user_repo.add_user(session, admin)
        logger.info("Bootstrapped initial administrator user '%s' from environment", init_user)

    async def get_public_auth_status(
        self,
        principal: AuthenticatedUser | None,
    ) -> dict[str, Any]:
        """
        获取前端初始化引导、登录方式及当前登录态综合状态（无需鉴权）。

        @param principal: 从当前请求解析出的已登录用户或 None
        @return: 鉴权状态摘要字典
        """
        async with self._session_factory() as session:
            user_count = await self._user_repo.count_users(session)
        dynamic = await self._setting_service.get_settings()

        sso_ready = bool(
            dynamic.sso_enabled and dynamic.sso_issuer_url and dynamic.sso_client_id
        )
        return {
            "initialized": user_count > 0,
            "auth_required": self._settings.auth_require_login,
            "registration_mode": dynamic.registration_mode,
            "authenticated": principal is not None,
            "sso_enabled": sso_ready,
            "sso_provider_name": dynamic.sso_provider_name or "ZITADEL",
            "upgrade_planner_enabled": dynamic.upgrade_planner_enabled,
            "app_version": APP_VERSION,
            "github_url": GITHUB_REPO_URL,
            "user": (
                {
                    "id": principal.id,
                    "username": principal.username,
                    "display_name": principal.display_name,
                    "email": principal.email,
                    "role": principal.role,
                    "is_active": principal.is_active,
                    "totp_enabled": principal.totp_enabled,
                    "has_password": principal.has_password,
                    "oidc_bound": principal.oidc_bound,
                }
                if principal is not None
                else None
            ),
        }

    async def setup_initial_admin(
        self,
        username: str,
        password: str,
        display_name: str = "",
    ) -> LoginOutcome:
        """
        在系统尚未存在任何用户时创建首个管理员账号并自动完成登录。

        @param username: 管理员用户名
        @param password: 管理员密码（不少于 8 位）
        @param display_name: 显示昵称
        @return: 包含 JWT 与管理员信息的 LoginOutcome
        @raise AuthError: 系统已初始化或参数非法时抛出
        """
        clean_name = username.strip()
        if len(clean_name) < 2:
            raise AuthError("invalid_input", "用户名至少需要 2 个字符")
        if len(password) < 8:
            raise AuthError("invalid_input", "密码长度至少需要 8 位")

        async with self._session_factory() as session:
            async with session.begin():
                if await self._user_repo.count_users(session) > 0:
                    raise AuthError(
                        "already_initialized",
                        "系统已完成初始化，请使用账号密码或 SSO 登录",
                    )
                now = utc_now()
                admin = User(
                    username=clean_name,
                    display_name=display_name.strip() or clean_name,
                    email="",
                    password_hash=hash_password(password),
                    role="admin",
                    is_active=True,
                    token_version=1,
                    last_login_at=now,
                    created_at=now,
                    updated_at=now,
                )
                await self._user_repo.add_user(session, admin)
                token, exp = self._issue_session_token(admin)
                principal = _user_to_principal(admin)

        logger.info("Initial administrator user '%s' created via web setup", clean_name)
        return LoginOutcome(
            mfa_required=False,
            access_token=token,
            expires_at=exp,
            user=principal,
        )

    async def login_with_password(self, username: str, password: str) -> LoginOutcome:
        """
        执行用户名密码登录校验；若该账号启用了 OTP，则签发短期二次验证令牌。

        @param username: 用户名
        @param password: 明文密码
        @return: LoginOutcome 结果
        @raise AuthError: 凭据错误或账号已禁用时抛出
        """
        clean_user = username.strip()
        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_username(session, clean_user)
                stored_hash = user.password_hash if user is not None else None
                ok = verify_password(password, stored_hash)
                if user is None or not ok:
                    logger.warning("Failed password login attempt for username='%s'", clean_user)
                    raise AuthError("invalid_credentials", "用户名或密码错误")
                if not user.is_active:
                    raise AuthError("user_disabled", "该账号已被管理员禁用")

                if user.totp_enabled and user.totp_secret_encrypted:
                    mfa_token = self._issue_mfa_token(user)
                    return LoginOutcome(mfa_required=True, mfa_token=mfa_token)

                user.last_login_at = utc_now()
                token, exp = self._issue_session_token(user)
                principal = _user_to_principal(user)

        logger.info("User '%s' logged in with password", principal.username)
        return LoginOutcome(
            mfa_required=False,
            access_token=token,
            expires_at=exp,
            user=principal,
        )

    async def verify_login_otp(self, mfa_token: str, otp_code: str) -> LoginOutcome:
        """
        校验二步验证挑战令牌与 6 位 TOTP 动态口令并签发正式会话。

        @param mfa_token: 密码登录阶段返回的短期 mfa_token
        @param otp_code: 用户身份验证器生成的 6 位数字验证码
        @return: LoginOutcome 结果
        @raise AuthError: 令牌无效、过期或动态码不匹配时抛出
        """
        try:
            payload = jwt.decode(mfa_token, self._secret_key, algorithms=["HS256"])
        except InvalidTokenError as exc:
            raise AuthError("invalid_token", "二步验证会话已失效，请重新输入账号密码") from exc

        if payload.get("purpose") != "mfa_challenge":
            raise AuthError("invalid_token", "非法的二步验证令牌")

        user_id = int(payload.get("sub") or 0)
        token_ver = int(payload.get("ver") or -1)
        clean_code = otp_code.strip().replace(" ", "")

        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_id(session, user_id)
                if user is None or not user.is_active or user.token_version != token_ver:
                    raise AuthError("invalid_token", "账号状态已变更，请重新登录")
                if not user.totp_enabled or not user.totp_secret_encrypted:
                    raise AuthError("invalid_otp", "当前账号未开启 OTP 二步验证")

                secret = decrypt_secret(user.totp_secret_encrypted, self._secret_key)
                if not pyotp.TOTP(secret).verify(clean_code, valid_window=1):
                    logger.warning("Failed OTP challenge for user '%s'", user.username)
                    raise AuthError("invalid_otp", "OTP 动态口令无效或已过期")

                user.last_login_at = utc_now()
                token, exp = self._issue_session_token(user)
                principal = _user_to_principal(user)

        logger.info("User '%s' completed TOTP second-factor login", principal.username)
        return LoginOutcome(
            mfa_required=False,
            access_token=token,
            expires_at=exp,
            user=principal,
        )

    async def verify_session_token(self, token: str) -> AuthenticatedUser | None:
        """
        验证正式会话 JWT 并实时核对数据库启用状态与 token_version。

        @param token: Bearer 或 Cookie 携带的 JWT 字符串
        @return: AuthenticatedUser 对象，校验失败返回 None
        """
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=["HS256"])
        except InvalidTokenError:
            return None

        if payload.get("purpose") != "session":
            return None

        try:
            user_id = int(payload["sub"])
            token_ver = int(payload["ver"])
        except (KeyError, TypeError, ValueError):
            return None

        async with self._session_factory() as session:
            user = await self._user_repo.get_by_id(session, user_id)
            if user is None or not user.is_active or user.token_version != token_ver:
                return None
            return _user_to_principal(user)

    async def get_current_user_summary(self, user_id: int) -> dict[str, Any] | None:
        """
        获取当前已登录用户详细资料。

        @param user_id: 用户主键 ID
        @return: 用户摘要字典或 None
        """
        async with self._session_factory() as session:
            user = await self._user_repo.get_by_id(session, user_id)
            return _user_to_summary(user) if user else None

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> tuple[str, datetime]:
        """
        修改当前用户密码并递增 token_version 以吊销其他旧会话，同时返回新会话凭据。

        @param user_id: 当前用户主键 ID
        @param current_password: 原密码（若原 SSO 账号尚未设置本地密码可为空）
        @param new_password: 新密码（至少 8 位）
        @return: (新会话 JWT, 过期时间)
        @raise AuthError: 原密码不符或新密码不符合复杂度要求时抛出
        """
        if len(new_password) < 8:
            raise AuthError("invalid_input", "新密码长度至少需要 8 位")

        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_id(session, user_id)
                if user is None:
                    raise AuthError("not_found", "用户不存在")
                if user.password_hash and not verify_password(current_password, user.password_hash):
                    raise AuthError("invalid_credentials", "原密码输入不正确")
                user.password_hash = hash_password(new_password)
                user.token_version += 1
                user.updated_at = utc_now()
                token, exp = self._issue_session_token(user)

        logger.info("User id=%d changed password and rotated session version", user_id)
        return token, exp

    async def setup_totp(self, user_id: int) -> dict[str, str]:
        """
        为当前用户生成新的待绑定 TOTP 密钥、otpauth 配对链接与内联 SVG 二维码。

        @param user_id: 用户主键 ID
        @return: 包含 secret、otpauth_uri 与 qr_svg 的字典
        @raise AuthError: 用户不存在时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_id(session, user_id)
                if user is None:
                    raise AuthError("not_found", "用户不存在")
                secret = pyotp.random_base32()
                user.totp_pending_encrypted = encrypt_secret(secret, self._secret_key)
                user.updated_at = utc_now()
                uri = pyotp.TOTP(secret).provisioning_uri(
                    name=user.username, issuer_name="CCUJailer"
                )
        svg = _render_totp_qr_svg(uri)
        return {"secret": secret, "otpauth_uri": uri, "qr_svg": svg}

    async def enable_totp(self, user_id: int, otp_code: str) -> dict[str, Any]:
        """
        校验扫码后生成的 6 位动态口令并正式启用 OTP 二步验证。

        @param user_id: 用户主键 ID
        @param otp_code: 6 位数字动态码
        @return: 更新后的用户摘要字典
        @raise AuthError: 尚未发起绑定或动态码验证失败时抛出
        """
        clean_code = otp_code.strip().replace(" ", "")
        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_id(session, user_id)
                if user is None:
                    raise AuthError("not_found", "用户不存在")
                if not user.totp_pending_encrypted:
                    raise AuthError("invalid_input", "请先点击生成 OTP 绑定二维码")
                pending_secret = decrypt_secret(user.totp_pending_encrypted, self._secret_key)
                if not pyotp.TOTP(pending_secret).verify(clean_code, valid_window=1):
                    raise AuthError("invalid_otp", "动态口令不匹配，请检查手机时间是否同步")
                user.totp_secret_encrypted = encrypt_secret(pending_secret, self._secret_key)
                user.totp_pending_encrypted = None
                user.totp_enabled = True
                user.updated_at = utc_now()
                summary = _user_to_summary(user)

        logger.info("User id=%d enabled TOTP 2FA", user_id)
        return summary

    async def disable_totp(
        self,
        user_id: int,
        otp_code: str = "",
        current_password: str = "",
    ) -> dict[str, Any]:
        """
        校验动态口令或当前密码后停用并清空账号的 OTP 二步验证配置。

        @param user_id: 用户主键 ID
        @param otp_code: 可选 6 位 OTP 口令
        @param current_password: 可选当前密码（无 OTP 设备时使用）
        @return: 更新后的用户摘要字典
        @raise AuthError: 凭据校验失败时抛出
        """
        clean_code = otp_code.strip().replace(" ", "")
        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_id(session, user_id)
                if user is None:
                    raise AuthError("not_found", "用户不存在")
                verified = False
                if clean_code and user.totp_secret_encrypted:
                    secret = decrypt_secret(user.totp_secret_encrypted, self._secret_key)
                    verified = pyotp.TOTP(secret).verify(clean_code, valid_window=1)
                if not verified and current_password and user.password_hash:
                    verified = verify_password(current_password, user.password_hash)
                if not verified:
                    raise AuthError(
                        "invalid_credentials",
                        "请提供有效的动态口令或当前登录密码以解绑 OTP",
                    )

                user.totp_enabled = False
                user.totp_secret_encrypted = None
                user.totp_pending_encrypted = None
                user.updated_at = utc_now()
                summary = _user_to_summary(user)

        logger.info("User id=%d disabled TOTP 2FA", user_id)
        return summary

    async def _fetch_oidc_discovery(self, issuer_url: str) -> dict[str, Any]:
        """
        拉取 ZITADEL / 标准 OIDC Provider Discovery 元数据文档。

        @param issuer_url: OIDC Issuer 根地址
        @return: Discovery JSON 字典
        @raise AuthError: 拉取失败或缺少关键端点字段时抛出
        """
        discovery_url = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
        try:
            resp = await self._net.proxy_client.get(discovery_url, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OIDC discovery failed (%s): %s", discovery_url, exc)
            raise AuthError(
                "sso_failed",
                "无法获取 SSO OIDC Discovery 配置，请检查 Issuer URL",
            ) from exc

        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if not data.get(required):
                raise AuthError("sso_failed", f"OIDC Discovery 缺少必需字段 {required}")
        return data

    async def build_sso_authorize_url(self, redirect_uri: str) -> str:
        """
        构造 ZITADEL Authorization Code + PKCE（S256 无 Client Secret）授权跳转 URL。

        @param redirect_uri: 前端 SSO 回调完整 URL（须与 ZITADEL 配置一致）
        @return: 完整的授权跳转 URL
        @raise AuthError: SSO 未启用或配置不完整时抛出
        """
        dynamic = await self._setting_service.get_settings()
        if not dynamic.sso_enabled or not dynamic.sso_issuer_url or not dynamic.sso_client_id:
            raise AuthError("sso_disabled", "管理员尚未启用或完整配置 OIDC SSO")

        discovery = await self._fetch_oidc_discovery(dynamic.sso_issuer_url)
        verifier = secrets.token_urlsafe(64)
        challenge = _build_pkce_challenge(verifier)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)

        now = utc_now()
        expires = now + timedelta(minutes=10)
        async with self._session_factory() as session:
            async with session.begin():
                await self._user_repo.delete_expired_oidc_states(session, now)
                await self._user_repo.add_oidc_state(
                    session,
                    OidcState(
                        state=state,
                        code_verifier=verifier,
                        nonce=nonce,
                        redirect_uri=redirect_uri,
                        expires_at=expires,
                        created_at=now,
                    ),
                )

        query = urlencode(
            {
                "client_id": dynamic.sso_client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": dynamic.sso_scopes or "openid profile email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        auth_ep = str(discovery["authorization_endpoint"])
        separator = "&" if "?" in auth_ep else "?"
        return f"{auth_ep}{separator}{query}"

    async def complete_sso_callback(
        self,
        code: str,
        state: str,
        redirect_uri: str,
    ) -> LoginOutcome:
        """
        处理 ZITADEL OIDC 授权回调：无密钥提交 PKCE 参数换取令牌并校验 ID Token。

        @param code: 授权服务器回调的 Authorization Code
        @param state: 回调返回的防伪状态值
        @param redirect_uri: 本次回调地址（须与发起时完全一致）
        @return: 登录成功的 LoginOutcome
        @raise AuthError: 状态无效、换票失败、签名验证不通过或未允许注册时抛出
        """
        dynamic = await self._setting_service.get_settings()
        if not dynamic.sso_enabled or not dynamic.sso_issuer_url or not dynamic.sso_client_id:
            raise AuthError("sso_disabled", "SSO 登录未开启")

        now = utc_now()
        async with self._session_factory() as session:
            async with session.begin():
                await self._user_repo.delete_expired_oidc_states(session, now)
                state_row = await self._user_repo.pop_oidc_state(session, state)

        if (
            state_row is None
            or ensure_utc(state_row.expires_at) < now  # type: ignore[operator]
            or state_row.redirect_uri != redirect_uri
        ):
            raise AuthError("sso_failed", "SSO 登录状态已失效或不匹配，请重新发起登录")

        discovery = await self._fetch_oidc_discovery(dynamic.sso_issuer_url)
        token_ep = str(discovery["token_endpoint"])
        jwks_uri = str(discovery["jwks_uri"])
        issuer = str(discovery["issuer"]).rstrip("/")

        # 1. 无 client_secret 交换 Authorization Code（Pure Public Client PKCE Flow）
        form_data = {
            "grant_type": "authorization_code",
            "client_id": dynamic.sso_client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": state_row.code_verifier,
        }
        try:
            token_resp = await self._net.proxy_client.post(
                token_ep,
                data=form_data,
                headers={"Accept": "application/json"},
                timeout=20.0,
            )
            token_resp.raise_for_status()
            token_payload = token_resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OIDC PKCE token exchange failed: %s", exc)
            raise AuthError(
                "sso_failed",
                "SSO 授权码换取令牌失败，请检查 ZITADEL PKCE 客户端配置",
            ) from exc

        id_token = token_payload.get("id_token")
        if not id_token:
            raise AuthError("sso_failed", "SSO 令牌响应中缺少 id_token")

        # 2. 拉取 JWKS 公钥集并对 id_token 验签及核对 iss / aud / nonce
        try:
            jwks_resp = await self._net.proxy_client.get(jwks_uri, timeout=15.0)
            jwks_resp.raise_for_status()
            jwk_set = PyJWKSet.from_dict(jwks_resp.json())
            unverified_header = jwt.get_unverified_header(id_token)
            signing_key = next(
                (
                    key.key
                    for key in jwk_set.keys
                    if not unverified_header.get("kid") or key.key_id == unverified_header["kid"]
                ),
                None,
            )
            if signing_key is None:
                raise InvalidTokenError("No matching JWK key found for id_token kid")
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=_ALLOWED_ID_TOKEN_ALGS,
                audience=dynamic.sso_client_id,
                issuer=str(discovery["issuer"]),
            )
        except (httpx.HTTPError, ValueError, InvalidTokenError, KeyError) as exc:
            logger.warning("OIDC id_token verification failed: %s", exc)
            raise AuthError("sso_failed", "SSO 身份令牌签名校验失败") from exc

        if claims.get("nonce") != state_row.nonce:
            raise AuthError("sso_failed", "SSO 身份令牌 nonce 校验失败")

        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AuthError("sso_failed", "OIDC 身份令牌缺少 sub 唯一标识")

        # 3. 如需补充姓名/邮箱资料且存在 access_token 与 userinfo_endpoint，拉取 UserInfo
        userinfo_claims: dict[str, Any] = {}
        access_token = token_payload.get("access_token")
        userinfo_ep = discovery.get("userinfo_endpoint")
        if (
            access_token
            and userinfo_ep
            and (not claims.get("preferred_username") or not claims.get("email"))
        ):
            try:
                ui_resp = await self._net.proxy_client.get(
                    str(userinfo_ep),
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
                if ui_resp.is_success:
                    ui_data = ui_resp.json()
                    if not ui_data.get("sub") or str(ui_data["sub"]) == subject:
                        userinfo_claims = ui_data
            except (httpx.HTTPError, ValueError):
                pass

        preferred = str(
            claims.get("preferred_username")
            or userinfo_claims.get("preferred_username")
            or claims.get("nickname")
            or userinfo_claims.get("nickname")
            or ""
        ).strip()
        email = str(claims.get("email") or userinfo_claims.get("email") or "").strip()
        full_name = str(
            claims.get("name") or userinfo_claims.get("name") or preferred or email or subject
        ).strip()

        # 4. 查找或按策略自动注册用户并签发系统会话
        async with self._session_factory() as session:
            async with session.begin():
                user = await self._user_repo.get_by_oidc(session, issuer, subject)
                if user is None:
                    total_users = await self._user_repo.count_users(session)
                    if total_users > 0 and not dynamic.sso_auto_register:
                        raise AuthError(
                            "sso_failed",
                            "当前未开启 SSO 自动注册，请联系管理员预先开通账号",
                        )

                    email_prefix = email.split("@", 1)[0] if "@" in email else ""
                    base_candidate = preferred or email_prefix or subject[:16]
                    cleaned_name = _USERNAME_SAFE_RE.sub("_", base_candidate).strip("._-")
                    sanitized = (cleaned_name or f"sso_{subject[:8]}")[:56]
                    candidate = sanitized
                    suffix = 1
                    while await self._user_repo.get_by_username(session, candidate) is not None:
                        candidate = f"{sanitized}_{suffix}"
                        suffix += 1

                    role: UserRole = "admin" if total_users == 0 else dynamic.sso_default_role
                    user = User(
                        username=candidate,
                        display_name=full_name[:120],
                        email=email[:255],
                        password_hash=None,
                        role=role,
                        is_active=True,
                        oidc_issuer=issuer,
                        oidc_sub=subject,
                        token_version=1,
                        last_login_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    await self._user_repo.add_user(session, user)
                    logger.info("Provisioned SSO user '%s' (role=%s)", user.username, role)
                else:
                    if not user.is_active:
                        raise AuthError("user_disabled", "该账号已被管理员禁用")
                    if full_name:
                        user.display_name = full_name[:120]
                    if email:
                        user.email = email[:255]
                    user.last_login_at = now
                    user.updated_at = now

                jwt_token, exp = self._issue_session_token(user)
                principal = _user_to_principal(user)

        logger.info("User '%s' logged in via OIDC PKCE SSO", principal.username)
        return LoginOutcome(
            mfa_required=False,
            access_token=jwt_token,
            expires_at=exp,
            user=principal,
        )

    async def list_users(self) -> list[dict[str, Any]]:
        """
        列出系统内全部用户（管理员用）。

        @return: 用户摘要字典列表
        """
        async with self._session_factory() as session:
            users = await self._user_repo.list_all(session)
        return [_user_to_summary(u) for u in users]

    async def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        email: str = "",
        role: UserRole = "user",
        is_active: bool = True,
    ) -> dict[str, Any]:
        """
        管理员创建新本地用户账号。

        @param username: 登录用户名
        @param password: 初始密码（至少 8 位）
        @param display_name: 显示昵称
        @param email: 电子邮箱
        @param role: 角色（admin 或 user）
        @param is_active: 是否立即启用
        @return: 新建用户摘要字典
        @raise AuthError: 用户名冲突或参数校验不通过时抛出
        """
        clean_user = username.strip()
        if len(clean_user) < 2:
            raise AuthError("invalid_input", "用户名至少需要 2 个字符")
        if len(password) < 8:
            raise AuthError("invalid_input", "初始密码长度至少需要 8 位")

        async with self._session_factory() as session:
            async with session.begin():
                if await self._user_repo.get_by_username(session, clean_user) is not None:
                    raise AuthError("conflict", f"用户名 '{clean_user}' 已存在")
                now = utc_now()
                new_user = User(
                    username=clean_user,
                    display_name=display_name.strip() or clean_user,
                    email=email.strip(),
                    password_hash=hash_password(password),
                    role=role,
                    is_active=is_active,
                    token_version=1,
                    created_at=now,
                    updated_at=now,
                )
                await self._user_repo.add_user(session, new_user)
                summary = _user_to_summary(new_user)

        logger.info("Admin created user '%s' (role=%s)", clean_user, role)
        return summary

    async def update_user(
        self,
        actor_user_id: int,
        target_user_id: int,
        display_name: str | None = None,
        email: str | None = None,
        role: UserRole | None = None,
        is_active: bool | None = None,
        new_password: str | None = None,
    ) -> dict[str, Any]:
        """
        管理员更新指定用户资料、角色、启用状态或重置密码（含防最后管理员自锁校验）。

        @param actor_user_id: 当前执行操作的管理员 ID
        @param target_user_id: 目标用户 ID
        @param display_name: 可选新显示名
        @param email: 可选新邮箱
        @param role: 可选新角色
        @param is_active: 可选新启用状态
        @param new_password: 可选重置明文密码
        @return: 更新后的用户摘要字典
        @raise AuthError: 目标不存在、触发防自锁保护或参数非法时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                target = await self._user_repo.get_by_id(session, target_user_id)
                if target is None:
                    raise AuthError("not_found", "未找到指定用户")

                if target.id == actor_user_id and is_active is False:
                    raise AuthError("forbidden", "无法禁用当前正在登录的管理员账号")

                will_lose_admin = target.role == "admin" and target.is_active and (
                    (role is not None and role != "admin") or (is_active is False)
                )
                if will_lose_admin:
                    active_admins = await self._user_repo.count_active_admins(session)
                    if active_admins <= 1:
                        raise AuthError("last_admin", "系统必须至少保留一名处于启用状态的管理员")

                rotate_session = False
                if display_name is not None:
                    target.display_name = display_name.strip() or target.username
                if email is not None:
                    target.email = email.strip()
                if role is not None and role != target.role:
                    target.role = role
                    rotate_session = True
                if is_active is not None and is_active != target.is_active:
                    target.is_active = is_active
                    if not is_active:
                        rotate_session = True
                if new_password is not None and new_password != "":
                    if len(new_password) < 8:
                        raise AuthError("invalid_input", "重置密码长度至少需要 8 位")
                    target.password_hash = hash_password(new_password)
                    rotate_session = True

                if rotate_session:
                    target.token_version += 1
                target.updated_at = utc_now()
                summary = _user_to_summary(target)

        logger.info("Admin id=%d updated user id=%d", actor_user_id, target_user_id)
        return summary

    async def reset_user_otp(self, target_user_id: int) -> dict[str, Any]:
        """
        管理员强制清空指定用户的 OTP 二步验证配置（当用户丢失验证器设备时使用）。

        @param target_user_id: 目标用户 ID
        @return: 更新后的用户摘要字典
        @raise AuthError: 用户不存在时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                target = await self._user_repo.get_by_id(session, target_user_id)
                if target is None:
                    raise AuthError("not_found", "未找到指定用户")
                target.totp_enabled = False
                target.totp_secret_encrypted = None
                target.totp_pending_encrypted = None
                target.updated_at = utc_now()
                summary = _user_to_summary(target)

        logger.info("Admin reset OTP 2FA for user id=%d", target_user_id)
        return summary

    async def delete_user(self, actor_user_id: int, target_user_id: int) -> bool:
        """
        管理员删除指定用户账号（禁止删除自己与最后一个活跃管理员）。

        @param actor_user_id: 当前执行操作的管理员 ID
        @param target_user_id: 目标用户 ID
        @return: 成功删除返回 True
        @raise AuthError: 目标不存在或触发自锁保护时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                target = await self._user_repo.get_by_id(session, target_user_id)
                if target is None:
                    raise AuthError("not_found", "未找到指定用户")
                if target.id == actor_user_id:
                    raise AuthError("forbidden", "禁止删除当前正在登录的管理员账号")
                if target.role == "admin" and target.is_active:
                    active_admins = await self._user_repo.count_active_admins(session)
                    if active_admins <= 1:
                        raise AuthError("last_admin", "系统必须至少保留一名处于启用状态的管理员")
                await self._user_repo.delete_user(session, target)

        logger.info("Admin id=%d deleted user id=%d", actor_user_id, target_user_id)
        return True

    async def register_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        email: str = "",
        invite_code: str = "",
    ) -> LoginOutcome:
        """
        根据当前注册策略（开放注册 / 仅限邀请制 / 关闭注册）注册新本地账号并自动登录。

        @param username: 登录用户名（至少 2 个字符）
        @param password: 登录密码（至少 8 位）
        @param display_name: 显示昵称
        @param email: 电子邮箱
        @param invite_code: 邀请制模式下提交的邀请码
        @return: 包含 JWT 与新账号信息的 LoginOutcome
        @raise AuthError: 未初始化、注册已关闭、邀请码无效/已用完/过期或用户名冲突时抛出
        """
        clean_user = username.strip()
        if len(clean_user) < 2:
            raise AuthError("invalid_input", "用户名至少需要 2 个字符")
        if len(password) < 8:
            raise AuthError("invalid_input", "密码长度至少需要 8 位")

        dynamic = await self._setting_service.get_settings()
        mode: RegistrationMode = dynamic.registration_mode

        async with self._session_factory() as session:
            async with session.begin():
                if await self._user_repo.count_users(session) == 0:
                    raise AuthError("not_initialized", "系统尚未完成初始化，请先创建首个管理员")
                if mode == "closed":
                    raise AuthError(
                        "registration_closed",
                        "当前系统已关闭用户自助注册，请联系管理员开通账号",
                    )

                if await self._user_repo.get_by_username(session, clean_user) is not None:
                    raise AuthError("conflict", f"用户名 '{clean_user}' 已存在")

                now = utc_now()
                assigned_role: UserRole = "user"
                if mode == "invite_only":
                    clean_code = invite_code.strip().upper()
                    if not clean_code:
                        raise AuthError("invalid_invite", "当前仅限受邀注册，请填写有效邀请码")
                    invite = await self._user_repo.get_invite_by_code(session, clean_code)
                    exp = ensure_utc(invite.expires_at) if invite and invite.expires_at else None
                    if (
                        invite is None
                        or not invite.is_active
                        or invite.used_count >= invite.max_uses
                        or (exp is not None and exp <= now)
                    ):
                        raise AuthError("invalid_invite", "邀请码无效、已禁用、已用完或已过期")
                    invite.used_count += 1
                    invite.last_used_at = now
                    assigned_role = "admin" if invite.role == "admin" else "user"

                new_user = User(
                    username=clean_user,
                    display_name=display_name.strip() or clean_user,
                    email=email.strip(),
                    password_hash=hash_password(password),
                    role=assigned_role,
                    is_active=True,
                    token_version=1,
                    last_login_at=now,
                    created_at=now,
                    updated_at=now,
                )
                await self._user_repo.add_user(session, new_user)
                await session.flush()
                token, exp = self._issue_session_token(new_user)
                principal = _user_to_principal(new_user)

        logger.info(
            "User '%s' registered (mode=%s, role=%s)",
            principal.username,
            mode,
            principal.role,
        )
        return LoginOutcome(
            mfa_required=False,
            access_token=token,
            expires_at=exp,
            user=principal,
        )

    async def list_invites(self) -> list[dict[str, Any]]:
        """
        列出系统全部邀请注册码（管理员用）。

        @return: 邀请码摘要字典列表
        """
        async with self._session_factory() as session:
            rows = await self._user_repo.list_invite_codes(session)
        return [_invite_to_summary(row) for row in rows]

    async def create_invite(
        self,
        actor_user_id: int,
        role: UserRole = "user",
        max_uses: int = 1,
        expires_in_hours: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """
        生成新的限次/限时注册邀请码。

        @param actor_user_id: 创建邀请码的管理员 ID（静态 ADMIN_TOKEN 为 0）
        @param role: 受邀账号默认角色（user 或 admin）
        @param max_uses: 允许最大使用次数
        @param expires_in_hours: 可选有效时长（小时），None 表示永不过期
        @param note: 备注标签
        @return: 新生成的邀请码摘要字典
        @raise AuthError: 当 max_uses 参数非法时抛出
        """
        if max_uses < 1 or max_uses > 1000:
            raise AuthError("invalid_input", "最大使用次数必须在 1 至 1000 之间")

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        now = utc_now()
        expires_at = now + timedelta(hours=expires_in_hours) if expires_in_hours else None

        async with self._session_factory() as session:
            async with session.begin():
                code = ""
                for _ in range(10):
                    parts = [
                        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)
                    ]
                    candidate = f"CJ-{'-'.join(parts)}"
                    if await self._user_repo.get_invite_by_code(session, candidate) is None:
                        code = candidate
                        break
                if not code:
                    raise AuthError("conflict", "生成唯一邀请码失败，请重试")

                invite = InviteCode(
                    code=code,
                    role="admin" if role == "admin" else "user",
                    note=note.strip()[:200],
                    max_uses=max_uses,
                    used_count=0,
                    is_active=True,
                    created_by_user_id=actor_user_id if actor_user_id > 0 else None,
                    expires_at=expires_at,
                    created_at=now,
                )
                await self._user_repo.add_invite_code(session, invite)
                summary = _invite_to_summary(invite)

        logger.info(
            "Admin id=%d generated invite code id=%d (role=%s, max_uses=%d)",
            actor_user_id,
            summary["id"],
            summary["role"],
            max_uses,
        )
        return summary

    async def set_invite_active(self, invite_id: int, is_active: bool) -> dict[str, Any]:
        """
        启用或停用指定邀请码。

        @param invite_id: 邀请码 ID
        @param is_active: 目标启用状态
        @return: 更新后的邀请码摘要字典
        @raise AuthError: 未找到邀请码时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                invite = await self._user_repo.get_invite_by_id(session, invite_id)
                if invite is None:
                    raise AuthError("not_found", "未找到指定邀请码")
                invite.is_active = is_active
                summary = _invite_to_summary(invite)

        logger.info("Set invite id=%d active=%s", invite_id, is_active)
        return summary

    async def delete_invite(self, invite_id: int) -> bool:
        """
        删除指定邀请码记录。

        @param invite_id: 邀请码 ID
        @return: 成功删除返回 True
        @raise AuthError: 未找到邀请码时抛出
        """
        async with self._session_factory() as session:
            async with session.begin():
                invite = await self._user_repo.get_invite_by_id(session, invite_id)
                if invite is None:
                    raise AuthError("not_found", "未找到指定邀请码")
                await self._user_repo.delete_invite_code(session, invite)

        logger.info("Deleted invite code id=%d", invite_id)
        return True
