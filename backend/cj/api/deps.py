"""
FastAPI 依赖注入、RBAC 角色鉴权与全站访问控制模块。

支持通过 Authorization Bearer JWT 与 HttpOnly cj_session Cookie 双通道传递会话，
并向后兼容可选的 ADMIN_TOKEN 管理员脚本令牌；同时落实默认全站强制登录策略。
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from cj.core.config import Settings
from cj.core.scheduler import BackgroundScheduler
from cj.core.security import verify_admin_token
from cj.services.auth_service import AuthenticatedUser, AuthService
from cj.services.ccu_service import CcuService
from cj.services.i18n_service import I18nService
from cj.services.setting_service import SettingService
from cj.services.webhook_service import WebhookService

SESSION_COOKIE_NAME = "cj_session"


def _extract_bearer_token(authorization: str | None) -> str | None:
    """
    从 Authorization 请求头提取 Bearer 凭据。

    @param authorization: Authorization 请求头原文
    @return: 提取到的令牌或 None
    """
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def get_settings(request: Request) -> Settings:
    """
    从应用状态容器提取静态配置实例。

    @param request: FastAPI 请求对象
    @return: 应用 Settings 实例
    """
    return request.app.state.container.settings


def get_ccu_service(request: Request) -> CcuService:
    """
    从应用状态容器提取 CCU 监控与商店聚合服务。

    @param request: FastAPI 请求对象
    @return: CcuService 实例
    """
    return request.app.state.container.ccu_service


def get_i18n_service(request: Request) -> I18nService:
    """
    从应用状态容器提取船名中文化服务。

    @param request: FastAPI 请求对象
    @return: I18nService 实例
    """
    return request.app.state.container.i18n_service


def get_webhook_service(request: Request) -> WebhookService:
    """
    从应用状态容器提取 Webhook 推送服务。

    @param request: FastAPI 请求对象
    @return: WebhookService 实例
    """
    return request.app.state.container.webhook_service


def get_setting_service(request: Request) -> SettingService:
    """
    从应用状态容器提取动态系统配置服务。

    @param request: FastAPI 请求对象
    @return: SettingService 实例
    """
    return request.app.state.container.setting_service


def get_auth_service(request: Request) -> AuthService:
    """
    从应用状态容器提取认证与用户管理服务。

    @param request: FastAPI 请求对象
    @return: AuthService 实例
    """
    return request.app.state.container.auth_service


def get_scheduler(request: Request) -> BackgroundScheduler:
    """
    从应用状态容器提取后台调度器实例。

    @param request: FastAPI 请求对象
    @return: BackgroundScheduler 实例
    """
    return request.app.state.container.scheduler


def _build_admin_token_principal() -> AuthenticatedUser:
    """
    构造静态 ADMIN_TOKEN 兼容访问主体。

    @return: 虚拟管理员 AuthenticatedUser 实例
    """
    return AuthenticatedUser(
        id=0,
        username="admin_token",
        display_name="ADMIN_TOKEN",
        email="",
        role="admin",
        is_active=True,
        totp_enabled=False,
        has_password=False,
        oidc_bound=False,
        passkey_count=0,
        preferences={},
    )


async def get_optional_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header()] = None,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser | None:
    """
    尝试从 Bearer JWT、HttpOnly Cookie 或向后兼容的 ADMIN_TOKEN 解析当前登录主体。

    当请求显式携带 Authorization Bearer 凭据时，不再静默回退到旧 Cookie，
    避免无效或非会话用途令牌（如 MFA 挑战令牌）被已有浏览器 Cookie 掩盖。

    @param request: FastAPI 请求对象
    @param settings: 应用静态配置
    @param auth_service: 认证服务
    @param authorization: Authorization 请求头
    @param x_admin_token: 可选兼容 X-Admin-Token 请求头
    @return: 通过校验的 AuthenticatedUser，未携带有效凭据时返回 None
    """
    bearer = _extract_bearer_token(authorization)
    expected_admin = settings.admin_token.strip()

    if bearer is not None:
        principal = await auth_service.verify_session_token(bearer)
        if principal is not None:
            return principal
        if expected_admin and verify_admin_token(expected_admin, bearer):
            return _build_admin_token_principal()
        return None

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token:
        principal = await auth_service.verify_session_token(cookie_token)
        if principal is not None:
            return principal

    # 向后兼容自动化脚本使用的静态 X-Admin-Token
    if expected_admin and x_admin_token:
        if verify_admin_token(expected_admin, x_admin_token.strip()):
            return _build_admin_token_principal()
    return None


async def require_authenticated_user(
    current_user: Annotated[AuthenticatedUser | None, Depends(get_optional_current_user)],
) -> AuthenticatedUser:
    """
    强制要求请求必须具备有效登录身份，否则返回 401。

    @param current_user: 已解析的登录主体或 None
    @return: 合法 AuthenticatedUser
    @raise HTTPException: 未登录或会话已失效时抛出 401
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录 CCUJailer 账号后再继续操作",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


async def require_site_access(
    current_user: Annotated[AuthenticatedUser | None, Depends(get_optional_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedUser | None:
    """
    全站读取权限守卫：当环境变量 AUTH_REQUIRE_LOGIN 开启（默认开启）时拒绝未登录访客。

    @param current_user: 当前请求解析的用户或 None
    @param settings: 应用静态配置实例
    @return: 已登录主体或允许公开访问时的 None
    @raise HTTPException: 需强制登录且当前未登录时抛出 401
    """
    if settings.auth_require_login and current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="当前站点已启用强制登录，请先登录后查看商店数据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


async def require_admin_user(
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
) -> AuthenticatedUser:
    """
    管理员权限守卫：要求已登录主体具备 admin 角色，否则返回 403。

    @param current_user: 已通过认证的当前用户
    @return: 具备管理员角色的 AuthenticatedUser
    @raise HTTPException: 非管理员角色尝试访问管理接口时抛出 403
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该操作仅限管理员执行",
        )
    return current_user
