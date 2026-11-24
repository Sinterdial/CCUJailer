"""
RESTful API v1 路由分发模块。

所有接口均以 /api/v1 为前缀，统一返回强类型 Pydantic 模型；
通过 RBAC 依赖实现全站强制登录（可配）、账号密码/TOTP/OIDC PKCE 认证、
用户管理、全量商店富化数据、升级路线规划以及可编程 Webhook 配置管理。
"""

from dataclasses import asdict
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from cj.api.deps import (
    SESSION_COOKIE_NAME,
    get_auth_service,
    get_ccu_service,
    get_i18n_service,
    get_optional_current_user,
    get_scheduler,
    get_setting_service,
    get_settings,
    get_webhook_service,
    require_admin_user,
    require_authenticated_user,
    require_site_access,
)
from cj.api.schemas import (
    AuthStatusResponse,
    AuthUserSchema,
    CcuItemResponse,
    CcuListResponse,
    CcuRefreshResponse,
    ChangelogItemResponse,
    ChangelogListResponse,
    ChangePasswordRequest,
    DeleteResponse,
    DynamicSettingsResponse,
    DynamicSettingsUpdateRequest,
    FullStoreResponse,
    I18nSyncResponse,
    InviteCodeCreateRequest,
    InviteCodeListResponse,
    InviteCodeSchema,
    InviteCodeToggleRequest,
    LoginOtpRequest,
    LoginRequest,
    LoginResponse,
    RegisterUserRequest,
    SetupAdminRequest,
    ShipI18nOverrideRequest,
    ShipI18nOverrideResponse,
    ShipItemResponse,
    ShipListResponse,
    SsoAuthorizeResponse,
    SsoCallbackRequest,
    StatusSummaryResponse,
    TotpDisableRequest,
    TotpSetupResponse,
    TotpVerifyRequest,
    UpgradeCostsResponse,
    UpgradePlanResponse,
    UserCreateRequest,
    UserListResponse,
    UserUpdateRequest,
    WebhookLogItemResponse,
    WebhookLogListResponse,
    WebhookRuleCreateRequest,
    WebhookRuleListResponse,
    WebhookRuleResponse,
    WebhookRuleUpdateRequest,
    WebhookTestResponse,
)
from cj.core.config import Settings, SystemConfigKey
from cj.core.scheduler import BackgroundScheduler
from cj.scraper.rsi_client import RsiScrapeError
from cj.services.auth_service import AuthenticatedUser, AuthError, AuthService, LoginOutcome
from cj.services.ccu_service import CcuService
from cj.services.i18n_service import I18nService
from cj.services.setting_service import DynamicSettingsSnapshot, SettingService
from cj.services.webhook_service import WebhookService

router = APIRouter(prefix="/api/v1")


def _raise_auth_http_error(exc: AuthError) -> NoReturn:
    """
    将领域层 AuthError 映射为对应的标准 HTTPException 状态码。

    @param exc: AuthError 领域异常实例
    @raise HTTPException: 转换后的标准 HTTP 异常
    """
    code_map = {
        "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
        "invalid_otp": status.HTTP_401_UNAUTHORIZED,
        "invalid_token": status.HTTP_401_UNAUTHORIZED,
        "user_disabled": status.HTTP_403_FORBIDDEN,
        "forbidden": status.HTTP_403_FORBIDDEN,
        "last_admin": status.HTTP_403_FORBIDDEN,
        "registration_closed": status.HTTP_403_FORBIDDEN,
        "not_found": status.HTTP_404_NOT_FOUND,
        "already_initialized": status.HTTP_409_CONFLICT,
        "not_initialized": status.HTTP_409_CONFLICT,
        "conflict": status.HTTP_409_CONFLICT,
        "invalid_invite": status.HTTP_400_BAD_REQUEST,
    }
    http_status = code_map.get(exc.code, status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=http_status, detail=exc.message) from exc


def _set_session_cookie(
    request: Request,
    response: Response,
    token: str,
    settings: Settings,
) -> None:
    """
    在响应上种下 HttpOnly 会话 Cookie，兼顾反向代理 HTTPS 自动识别。

    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param token: 已签发的会话 JWT
    @param settings: 应用静态配置
    """
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    is_https = request.url.scheme == "https" or forwarded_proto.lower() == "https"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_token_expire_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


def _to_login_response(
    outcome: LoginOutcome,
    request: Request,
    response: Response,
    settings: Settings,
) -> LoginResponse:
    """
    转换 LoginOutcome 为 LoginResponse，并在认证完成时种下会话 Cookie。

    @param outcome: 业务层登录结果
    @param request: 请求对象
    @param response: 响应对象
    @param settings: 应用静态配置
    @return: LoginResponse 模型
    """
    if not outcome.mfa_required and outcome.access_token:
        _set_session_cookie(request, response, outcome.access_token, settings)

    user_schema = (
        AuthUserSchema(
            id=outcome.user.id,
            username=outcome.user.username,
            display_name=outcome.user.display_name,
            email=outcome.user.email,
            role=outcome.user.role,
            is_active=outcome.user.is_active,
            totp_enabled=outcome.user.totp_enabled,
            has_password=outcome.user.has_password,
            oidc_bound=outcome.user.oidc_bound,
        )
        if outcome.user is not None
        else None
    )
    return LoginResponse(
        mfa_required=outcome.mfa_required,
        mfa_token=outcome.mfa_token,
        access_token=outcome.access_token,
        expires_at=outcome.expires_at,
        user=user_schema,
    )


def _to_settings_response(
    snapshot: DynamicSettingsSnapshot,
    settings: Settings,
) -> DynamicSettingsResponse:
    """
    将动态配置快照与只读密钥状态组装为公开响应 Schema。

    @param snapshot: 动态配置快照对象
    @param settings: 静态配置实例
    @return: DynamicSettingsResponse 模型
    """
    return DynamicSettingsResponse(
        scrape_interval_seconds=snapshot.scrape_interval_seconds,
        catalog_sync_interval_seconds=snapshot.catalog_sync_interval_seconds,
        i18n_source_url=snapshot.i18n_source_url,
        default_language=snapshot.default_language,
        log_retention_days=snapshot.log_retention_days,
        translate_enabled=snapshot.translate_enabled,
        translate_api_base=snapshot.translate_api_base,
        translate_model=snapshot.translate_model,
        translate_api_key_configured=bool(settings.translate_api_key.strip()),
        auth_require_login=settings.auth_require_login,
        registration_mode=snapshot.registration_mode,
        sso_enabled=snapshot.sso_enabled,
        sso_provider_name=snapshot.sso_provider_name,
        sso_issuer_url=snapshot.sso_issuer_url,
        sso_client_id=snapshot.sso_client_id,
        sso_scopes=snapshot.sso_scopes,
        sso_auto_register=snapshot.sso_auto_register,
        sso_default_role=snapshot.sso_default_role,
        upgrade_planner_enabled=snapshot.upgrade_planner_enabled,
    )


@router.get("/auth/status", response_model=AuthStatusResponse, tags=["Auth"])
async def get_auth_status(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    current_user: Annotated[AuthenticatedUser | None, Depends(get_optional_current_user)],
) -> AuthStatusResponse:
    """
    查询系统初始化状态、全站强制登录策略、SSO 启用状态及当前请求登录主体。

    @param auth_service: 认证业务服务
    @param current_user: 当前请求解析的用户或 None
    @return: AuthStatusResponse 模型
    """
    data = await auth_service.get_public_auth_status(current_user)
    user_data = data.pop("user", None)
    return AuthStatusResponse(
        **data,
        user=AuthUserSchema(**user_data) if user_data is not None else None,
    )


@router.post(
    "/auth/setup",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Auth"],
)
async def setup_initial_admin(
    payload: SetupAdminRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    首次启动无任何账号时通过页面创建初始管理员并自动签发会话凭据。

    @param payload: 初始管理员建号请求
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param auth_service: 认证服务
    @param settings: 静态配置实例
    @return: LoginResponse 登录凭据
    """
    try:
        outcome = await auth_service.setup_initial_admin(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return _to_login_response(outcome, request, response, settings)


@router.post(
    "/auth/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Auth"],
)
async def register_user(
    payload: RegisterUserRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    按当前注册策略（开放注册 / 仅限邀请制）注册新用户并自动签发会话凭据。

    @param payload: 注册请求体
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param auth_service: 认证服务
    @param settings: 静态配置实例
    @return: LoginResponse 登录凭据
    """
    try:
        outcome = await auth_service.register_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            email=payload.email,
            invite_code=payload.invite_code,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return _to_login_response(outcome, request, response, settings)


@router.post("/auth/login", response_model=LoginResponse, tags=["Auth"])
async def login_password(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    使用用户名与密码登录；若开启 OTP 二步验证则返回 mfa_required=true 与短期挑战令牌。

    @param payload: 账号密码请求体
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param auth_service: 认证服务
    @param settings: 静态配置实例
    @return: LoginResponse 结果模型
    """
    try:
        outcome = await auth_service.login_with_password(
            username=payload.username,
            password=payload.password,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return _to_login_response(outcome, request, response, settings)


@router.post("/auth/login/otp", response_model=LoginResponse, tags=["Auth"])
async def login_otp_challenge(
    payload: LoginOtpRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    验证 TOTP 动态口令以完成二步验证登录流程。

    @param payload: MFA 挑战令牌与 6 位动态口令请求
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param auth_service: 认证服务
    @param settings: 静态配置实例
    @return: LoginResponse 正式会话凭据
    """
    try:
        outcome = await auth_service.verify_login_otp(
            mfa_token=payload.mfa_token,
            otp_code=payload.otp_code,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return _to_login_response(outcome, request, response, settings)


@router.post("/auth/logout", response_model=DeleteResponse, tags=["Auth"])
async def logout(response: Response) -> DeleteResponse:
    """
    退出当前登录会话并清理服务端下发的 HttpOnly 会话 Cookie。

    @param response: HTTP 响应对象
    @return: DeleteResponse 确认状态
    """
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", samesite="lax")
    return DeleteResponse(deleted=True, id="session")


@router.get("/auth/sso/authorize", response_model=SsoAuthorizeResponse, tags=["Auth"])
async def get_sso_authorize_url(
    redirect_uri: Annotated[str, Query(min_length=8, description="OIDC 登录成功回调的前端 URL")],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> SsoAuthorizeResponse:
    """
    发起 ZITADEL OIDC Authorization Code + PKCE（S256 无 Secret 模式）登录跳转。

    @param redirect_uri: 前端回调 URL
    @param auth_service: 认证服务
    @return: SsoAuthorizeResponse 模型
    """
    try:
        url = await auth_service.build_sso_authorize_url(redirect_uri=redirect_uri)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return SsoAuthorizeResponse(authorize_url=url)


@router.post("/auth/sso/callback", response_model=LoginResponse, tags=["Auth"])
async def complete_sso_callback(
    payload: SsoCallbackRequest,
    request: Request,
    response: Response,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    完成 OIDC PKCE 无密钥换票与 ID Token 验签，完成 SSO 登录。

    @param payload: 包含授权码 code、state 与 redirect_uri 的请求体
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param auth_service: 认证服务
    @param settings: 静态配置
    @return: LoginResponse 正式会话凭据
    """
    try:
        outcome = await auth_service.complete_sso_callback(
            code=payload.code,
            state=payload.state,
            redirect_uri=payload.redirect_uri,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return _to_login_response(outcome, request, response, settings)


@router.get("/auth/me", response_model=AuthUserSchema, tags=["Auth"])
async def get_current_user_profile(
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    获取当前已登录账号的详细个人资料及安全开关状态。

    @param current_user: 当前登录主体
    @param auth_service: 认证服务
    @return: AuthUserSchema 模型
    """
    if current_user.id == 0:
        return AuthUserSchema(
            id=0,
            username=current_user.username,
            display_name=current_user.display_name,
            email=current_user.email,
            role=current_user.role,
            is_active=True,
            totp_enabled=False,
            has_password=False,
            oidc_bound=False,
        )
    summary = await auth_service.get_current_user_summary(current_user.id)
    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return AuthUserSchema(**summary)


@router.put("/auth/password", response_model=LoginResponse, tags=["Auth"])
async def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    """
    当前登录用户修改自己的密码并刷新当前会话凭据（同步吊销旧设备令牌）。

    @param payload: 改密请求体
    @param request: HTTP 请求对象
    @param response: HTTP 响应对象
    @param current_user: 当前登录用户
    @param auth_service: 认证服务
    @param settings: 静态配置
    @return: LoginResponse 更新后的会话凭据
    """
    if current_user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="静态 ADMIN_TOKEN 兼容会话不支持在线修改密码",
        )
    try:
        new_token, exp = await auth_service.change_password(
            user_id=current_user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)

    _set_session_cookie(request, response, new_token, settings)
    return LoginResponse(
        mfa_required=False,
        access_token=new_token,
        expires_at=exp,
        user=AuthUserSchema(
            id=current_user.id,
            username=current_user.username,
            display_name=current_user.display_name,
            email=current_user.email,
            role=current_user.role,
            is_active=current_user.is_active,
            totp_enabled=current_user.totp_enabled,
            has_password=True,
            oidc_bound=current_user.oidc_bound,
        ),
    )


@router.post("/auth/otp/setup", response_model=TotpSetupResponse, tags=["Auth"])
async def setup_own_totp(
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TotpSetupResponse:
    """
    为当前登录用户生成待绑定的 TOTP 密钥与本地内联二维码 SVG。

    @param current_user: 当前登录用户
    @param auth_service: 认证服务
    @return: TotpSetupResponse 模型
    """
    if current_user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="静态 ADMIN_TOKEN 会话不支持绑定 OTP",
        )
    try:
        res = await auth_service.setup_totp(current_user.id)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return TotpSetupResponse(**res)


@router.post("/auth/otp/enable", response_model=AuthUserSchema, tags=["Auth"])
async def enable_own_totp(
    payload: TotpVerifyRequest,
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    核验 6 位动态口令并启用当前账号的 TOTP 二步验证。

    @param payload: 6 位验证码请求体
    @param current_user: 当前登录用户
    @param auth_service: 认证服务
    @return: 更新后的 AuthUserSchema 模型
    """
    if current_user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="静态 ADMIN_TOKEN 会话不支持绑定 OTP",
        )
    try:
        summary = await auth_service.enable_totp(current_user.id, payload.otp_code)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return AuthUserSchema(**summary)


@router.post("/auth/otp/disable", response_model=AuthUserSchema, tags=["Auth"])
async def disable_own_totp(
    payload: TotpDisableRequest,
    current_user: Annotated[AuthenticatedUser, Depends(require_authenticated_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    校验当前 OTP 口令或密码后解除当前账号的二步验证绑定。

    @param payload: 解绑校验请求体
    @param current_user: 当前登录用户
    @param auth_service: 认证服务
    @return: 更新后的 AuthUserSchema 模型
    """
    if current_user.id == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="静态 ADMIN_TOKEN 会话不支持操作 OTP",
        )
    try:
        summary = await auth_service.disable_totp(
            user_id=current_user.id,
            otp_code=payload.otp_code,
            current_password=payload.current_password,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return AuthUserSchema(**summary)


@router.get(
    "/status",
    response_model=StatusSummaryResponse,
    dependencies=[Depends(require_site_access)],
    tags=["System"],
)
async def get_status(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
) -> StatusSummaryResponse:
    """
    获取当前系统的 CCU 监控数量、舰船汉化覆盖率与最近刷新时间。

    @param ccu_service: CCU 监控服务
    @return: StatusSummaryResponse 模型
    """
    summary = await ccu_service.get_status_summary()
    return StatusSummaryResponse(**summary)


@router.get(
    "/store",
    response_model=FullStoreResponse,
    dependencies=[Depends(require_site_access)],
    tags=["Store"],
)
async def get_full_store(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
) -> FullStoreResponse:
    """
    获取完整原版 PledgeWatch 商店、全船版本、CCU、游戏内买租、武器及货舱网格聚合视图。

    @param ccu_service: CCU 监控与商店聚合服务
    @return: FullStoreResponse 模型
    """
    payload = await ccu_service.get_full_store_payload()
    return FullStoreResponse(**payload)


@router.get(
    "/upgrades/{ref}/costs",
    response_model=UpgradeCostsResponse,
    dependencies=[Depends(require_site_access)],
    tags=["Store"],
)
async def get_ship_upgrade_costs(
    ref: int,
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
) -> UpgradeCostsResponse:
    """
    向 RSI 升级工具查询指定起始船当前可升级到的全部 SKU 及真实补差价（带 10 分钟缓存）。

    @param ref: 起始船在 RSI 升级工具中的 ship id
    @param ccu_service: CCU 监控服务
    @return: UpgradeCostsResponse 模型
    """
    try:
        res = await ccu_service.get_upgrade_costs(ref)
    except RsiScrapeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"无法连接 RSI 升级接口查询实时差价: {exc}",
        ) from exc

    if res is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到该起始船对应的 RSI 升级编号",
        )
    return UpgradeCostsResponse(**res)


@router.get(
    "/plan",
    response_model=UpgradePlanResponse,
    dependencies=[Depends(require_site_access)],
    tags=["Store"],
)
async def get_upgrade_plan_map(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
    setting_service: Annotated[SettingService, Depends(get_setting_service)],
) -> UpgradePlanResponse:
    """
    获取升级路线规划器（/plan）所需全量舰船节点、在售 CCU 与可达路径邻接图。

    @param ccu_service: CCU 监控服务
    @param setting_service: 动态系统配置服务
    @return: UpgradePlanResponse 模型
    @raise HTTPException: 当升级路线规划功能未开启时返回 403
    """
    snapshot = await setting_service.get_settings()
    if not snapshot.upgrade_planner_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="升级路线规划功能当前已关闭，可在「系统设置 -> 系统参数」中启用",
        )
    data = await ccu_service.get_upgrade_plan_map()
    return UpgradePlanResponse(**data)


@router.get(
    "/ccus",
    response_model=CcuListResponse,
    dependencies=[Depends(require_site_access)],
    tags=["CCUs"],
)
async def list_ccus(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
    q: Annotated[str | None, Query(description="双语关键词搜索（英文/中文船名或版本标题）")] = None,
    warbond_only: Annotated[bool, Query(description="是否仅返回 Warbond 优惠 CCU")] = False,
    min_price: Annotated[float | None, Query(ge=0, description="最低购买价（USD）")] = None,
    max_price: Annotated[float | None, Query(ge=0, description="最高购买价（USD）")] = None,
    min_savings: Annotated[float | None, Query(ge=0, description="最低节省金额（USD）")] = None,
) -> CcuListResponse:
    """
    获取当前在售 CCU 列表，并实时计算每条记录的最近可升级起始底船推荐。

    @param ccu_service: CCU 监控服务
    @param q: 双语搜索关键字
    @param warbond_only: 仅查看 Warbond 标识
    @param min_price: 最低价过滤
    @param max_price: 最高价过滤
    @param min_savings: 最低立省金额过滤
    @return: CcuListResponse 模型
    """
    rows = await ccu_service.list_active_ccus(
        query=q,
        warbond_only=warbond_only,
        min_price=min_price,
        max_price=max_price,
        min_savings=min_savings,
    )
    items = [CcuItemResponse(**row) for row in rows]
    return CcuListResponse(items=items, total=len(items))


@router.post(
    "/ccus/refresh",
    response_model=CcuRefreshResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["CCUs"],
)
async def trigger_ccu_refresh(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
    scheduler: Annotated[BackgroundScheduler, Depends(get_scheduler)],
    sync_catalog: Annotated[
        bool, Query(description="是否在 CCU Diff 前同步刷新一次舰船目录与单船商店")
    ] = False,
    enrich: Annotated[
        bool, Query(description="是否同时拉取 Wiki/UEX/货舱网格富化数据（耗时较长）")
    ] = False,
) -> CcuRefreshResponse:
    """
    管理员手动立即执行一次 RSI 单船商店、升级商店抓取与状态 Diff 检查。

    @param ccu_service: CCU 监控服务
    @param scheduler: 后台调度器（共享抓取互斥锁防止并发冲突）
    @param sync_catalog: 是否同时刷新 Ship Matrix 目录
    @param enrich: 是否执行完整第三方外部数据富化
    @return: CcuRefreshResponse 刷新结果模型
    """
    if enrich:
        await ccu_service.sync_catalog_and_enrichment()

    async with scheduler.scrape_lock:
        summary = await ccu_service.get_status_summary()
        if sync_catalog or summary["total_ships"] == 0:
            await ccu_service.sync_catalog_and_standalone()
        else:
            await ccu_service.sync_standalone_store()
        outcome = await ccu_service.sync_and_diff_ccus()
    return CcuRefreshResponse(**outcome)


@router.get(
    "/ships",
    response_model=ShipListResponse,
    dependencies=[Depends(require_site_access)],
    tags=["Ships"],
)
async def list_ships(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
    q: Annotated[str | None, Query(description="双语检索关键词")] = None,
    always_available_only: Annotated[bool, Query(description="是否仅筛选常驻可买底船")] = False,
) -> ShipListResponse:
    """
    获取全量舰船目录及其中英文译名映射表。

    @param ccu_service: CCU 监控服务
    @param q: 双语检索关键词
    @param always_available_only: 仅返回常驻可买船只
    @return: ShipListResponse 模型
    """
    rows = await ccu_service.list_ships(
        query=q,
        always_available_only=always_available_only,
    )
    items = [ShipItemResponse(**row) for row in rows]
    return ShipListResponse(items=items, total=len(items))


@router.put(
    "/ships/{ship_id}/i18n",
    response_model=ShipI18nOverrideResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Ships"],
)
async def override_ship_i18n(
    ship_id: int,
    payload: ShipI18nOverrideRequest,
    i18n_service: Annotated[I18nService, Depends(get_i18n_service)],
) -> ShipI18nOverrideResponse:
    """
    管理员手动覆盖或恢复指定舰船的中文译名。

    @param ship_id: 舰船主键 ID
    @param payload: 包含自定义中文名称的请求体
    @param i18n_service: 船名中文化服务
    @return: ShipI18nOverrideResponse 更新结果
    """
    res = await i18n_service.set_ship_override(ship_id=ship_id, name_zh=payload.name_zh)
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到指定舰船")
    return ShipI18nOverrideResponse(**res)


@router.get(
    "/changelog",
    response_model=ChangelogListResponse,
    dependencies=[Depends(require_site_access)],
    tags=["Changelog"],
)
async def list_changelog(
    ccu_service: Annotated[CcuService, Depends(get_ccu_service)],
    limit: Annotated[int, Query(ge=1, le=200, description="每页返回记录数")] = 50,
    offset: Annotated[int, Query(ge=0, description="分页偏移量")] = 0,
    event_type: Annotated[str | None, Query(description="按事件类型过滤")] = None,
    q: Annotated[str | None, Query(description="按中英文船名搜索")] = None,
) -> ChangelogListResponse:
    """
    分页查询 CCU 与单船商店历史上下架及调价日志。

    @param ccu_service: CCU 监控服务
    @param limit: 每页条数
    @param offset: 偏移量
    @param event_type: 事件类型过滤
    @param q: 搜索关键字
    @return: ChangelogListResponse 模型
    """
    rows, total = await ccu_service.list_changelog(
        limit=limit,
        offset=offset,
        event_type=event_type,
        query=q,
    )
    items = [ChangelogItemResponse(**row) for row in rows]
    return ChangelogListResponse(items=items, total=total)


@router.get(
    "/webhooks",
    response_model=WebhookRuleListResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def list_webhook_rules(
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> WebhookRuleListResponse:
    """
    获取全部 Webhook 通知规则（敏感请求头自动脱敏，仅限管理员）。

    @param webhook_service: Webhook 业务服务
    @return: WebhookRuleListResponse 模型
    """
    rows = await webhook_service.list_rules()
    items = [WebhookRuleResponse(**row) for row in rows]
    return WebhookRuleListResponse(items=items, total=len(items))


@router.post(
    "/webhooks",
    response_model=WebhookRuleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def create_webhook_rule(
    payload: WebhookRuleCreateRequest,
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> WebhookRuleResponse:
    """
    创建一条新的 Webhook 推送规则（仅限管理员）。

    @param payload: Webhook 规则创建请求体
    @param webhook_service: Webhook 业务服务
    @return: 创建完成且已脱敏的 WebhookRuleResponse
    """
    created = await webhook_service.create_rule(payload.model_dump())
    return WebhookRuleResponse(**created)


@router.get(
    "/webhooks/logs",
    response_model=WebhookLogListResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def list_webhook_logs(
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
    limit: Annotated[int, Query(ge=1, le=200, description="每页日志条数")] = 50,
    offset: Annotated[int, Query(ge=0, description="分页偏移量")] = 0,
    rule_id: Annotated[str | None, Query(description="按规则 ID 过滤")] = None,
    success: Annotated[bool | None, Query(description="按投递是否成功过滤")] = None,
) -> WebhookLogListResponse:
    """
    分页查询近期 Webhook 推送执行记录与失败诊断摘要（仅限管理员）。

    @param webhook_service: Webhook 业务服务
    @param limit: 单页条数
    @param offset: 偏移量
    @param rule_id: 规则 ID 过滤
    @param success: 成功状态过滤
    @return: WebhookLogListResponse 模型
    """
    rows, total = await webhook_service.list_logs(
        limit=limit,
        offset=offset,
        rule_id=rule_id,
        success=success,
    )
    items = [WebhookLogItemResponse(**row) for row in rows]
    return WebhookLogListResponse(items=items, total=total)


@router.put(
    "/webhooks/{rule_id}",
    response_model=WebhookRuleResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def update_webhook_rule(
    rule_id: str,
    payload: WebhookRuleUpdateRequest,
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> WebhookRuleResponse:
    """
    更新指定 ID 的 Webhook 规则；传入的脱敏 Header 占位符将自动保留库中原密钥。

    @param rule_id: 待更新规则 ID
    @param payload: 规则更新请求体
    @param webhook_service: Webhook 业务服务
    @return: 更新后的 WebhookRuleResponse
    @raise HTTPException: 当目标规则不存在时返回 404
    """
    updated = await webhook_service.update_rule(
        rule_id=rule_id,
        data=payload.model_dump(exclude_unset=True),
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到指定的 Webhook 规则",
        )
    return WebhookRuleResponse(**updated)


@router.delete(
    "/webhooks/{rule_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def delete_webhook_rule(
    rule_id: str,
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> DeleteResponse:
    """
    删除指定 ID 的 Webhook 规则及其关联的历史推送日志。

    @param rule_id: 待删除规则 ID
    @param webhook_service: Webhook 业务服务
    @return: DeleteResponse 确认模型
    @raise HTTPException: 当目标规则不存在时返回 404
    """
    deleted = await webhook_service.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到指定的 Webhook 规则",
        )
    return DeleteResponse(deleted=True, id=rule_id)


@router.post(
    "/webhooks/{rule_id}/test",
    response_model=WebhookTestResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Webhooks"],
)
async def test_webhook_rule(
    rule_id: str,
    webhook_service: Annotated[WebhookService, Depends(get_webhook_service)],
) -> WebhookTestResponse:
    """
    向指定 Webhook 规则发送一条模拟 CCU 测试推送以验证连通性与消息模板格式。

    @param rule_id: 待测试规则 ID
    @param webhook_service: Webhook 业务服务
    @return: WebhookTestResponse 测试推送详情
    @raise HTTPException: 当目标规则不存在时返回 404
    """
    outcome = await webhook_service.send_test_notification(rule_id)
    if outcome is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="未找到指定的 Webhook 规则",
        )
    return WebhookTestResponse(**asdict(outcome))


@router.post(
    "/i18n/sync",
    response_model=I18nSyncResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["i18n"],
)
async def sync_i18n_dictionary(
    i18n_service: Annotated[I18nService, Depends(get_i18n_service)],
) -> I18nSyncResponse:
    """
    管理员手动触发一次远程汉化词典拉取与全库舰船中英检索索引重建。

    @param i18n_service: 船名中文化服务
    @return: I18nSyncResponse 同步结果模型
    """
    summary = await i18n_service.sync_remote_dictionary()
    return I18nSyncResponse(**summary)


@router.get(
    "/settings",
    response_model=DynamicSettingsResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Settings"],
)
async def get_dynamic_settings(
    setting_service: Annotated[SettingService, Depends(get_setting_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DynamicSettingsResponse:
    """
    获取当前全部动态系统配置快照（含认证与 ZITADEL SSO 配置，仅限管理员）。

    @param setting_service: 动态配置服务
    @param settings: 静态配置实例
    @return: DynamicSettingsResponse 模型
    """
    snapshot = await setting_service.get_settings()
    return _to_settings_response(snapshot, settings)


@router.put(
    "/settings",
    response_model=DynamicSettingsResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Settings"],
)
async def update_dynamic_settings(
    payload: DynamicSettingsUpdateRequest,
    setting_service: Annotated[SettingService, Depends(get_setting_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DynamicSettingsResponse:
    """
    管理员热更新动态系统配置及认证/SSO 选项（立即生效，无需重启容器）。

    @param payload: 动态配置变更请求体
    @param setting_service: 动态配置服务
    @param settings: 静态配置实例
    @return: 更新后的 DynamicSettingsResponse 模型
    """
    updates: dict[SystemConfigKey, str] = {}
    if payload.scrape_interval_seconds is not None:
        updates[SystemConfigKey.SCRAPE_INTERVAL_SECONDS] = str(payload.scrape_interval_seconds)
    if payload.catalog_sync_interval_seconds is not None:
        updates[SystemConfigKey.CATALOG_SYNC_INTERVAL_SECONDS] = str(
            payload.catalog_sync_interval_seconds
        )
    if payload.i18n_source_url is not None:
        updates[SystemConfigKey.I18N_SOURCE_URL] = payload.i18n_source_url.strip()
    if payload.default_language is not None:
        updates[SystemConfigKey.DEFAULT_LANGUAGE] = payload.default_language
    if payload.log_retention_days is not None:
        updates[SystemConfigKey.LOG_RETENTION_DAYS] = str(payload.log_retention_days)
    if payload.translate_enabled is not None:
        updates[SystemConfigKey.TRANSLATE_ENABLED] = (
            "true" if payload.translate_enabled else "false"
        )
    if payload.translate_api_base is not None:
        updates[SystemConfigKey.TRANSLATE_API_BASE] = payload.translate_api_base.strip()
    if payload.translate_model is not None:
        updates[SystemConfigKey.TRANSLATE_MODEL] = payload.translate_model.strip()
    if payload.sso_enabled is not None:
        updates[SystemConfigKey.SSO_ENABLED] = "true" if payload.sso_enabled else "false"
    if payload.sso_provider_name is not None:
        updates[SystemConfigKey.SSO_PROVIDER_NAME] = (
            payload.sso_provider_name.strip() or "ZITADEL"
        )
    if payload.sso_issuer_url is not None:
        updates[SystemConfigKey.SSO_ISSUER_URL] = payload.sso_issuer_url.strip().rstrip("/")
    if payload.sso_client_id is not None:
        updates[SystemConfigKey.SSO_CLIENT_ID] = payload.sso_client_id.strip()
    if payload.sso_scopes is not None:
        updates[SystemConfigKey.SSO_SCOPES] = payload.sso_scopes.strip() or "openid profile email"
    if payload.sso_auto_register is not None:
        updates[SystemConfigKey.SSO_AUTO_REGISTER] = (
            "true" if payload.sso_auto_register else "false"
        )
    if payload.sso_default_role is not None:
        updates[SystemConfigKey.SSO_DEFAULT_ROLE] = payload.sso_default_role
    if payload.upgrade_planner_enabled is not None:
        updates[SystemConfigKey.UPGRADE_PLANNER_ENABLED] = (
            "true" if payload.upgrade_planner_enabled else "false"
        )
    if payload.registration_mode is not None:
        updates[SystemConfigKey.REGISTRATION_MODE] = payload.registration_mode

    snapshot = (
        await setting_service.update_settings(updates)
        if updates
        else await setting_service.get_settings()
    )
    return _to_settings_response(snapshot, settings)


@router.get(
    "/users",
    response_model=UserListResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def list_users(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserListResponse:
    """
    管理员查询系统全部受管用户账号列表。

    @param auth_service: 认证与用户管理服务
    @return: UserListResponse 模型
    """
    rows = await auth_service.list_users()
    items = [AuthUserSchema(**row) for row in rows]
    return UserListResponse(items=items, total=len(items))


@router.post(
    "/users",
    response_model=AuthUserSchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def create_user(
    payload: UserCreateRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    管理员创建新本地用户账号。

    @param payload: 新建用户请求体
    @param auth_service: 认证服务
    @return: AuthUserSchema 新建用户模型
    """
    try:
        created = await auth_service.create_user(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            email=payload.email,
            role=payload.role,
            is_active=payload.is_active,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return AuthUserSchema(**created)


@router.get(
    "/users/invites",
    response_model=InviteCodeListResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def list_invite_codes(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> InviteCodeListResponse:
    """
    管理员查询系统全部注册邀请码列表。

    @param auth_service: 认证与用户管理服务
    @return: InviteCodeListResponse 模型
    """
    rows = await auth_service.list_invites()
    items = [InviteCodeSchema(**row) for row in rows]
    return InviteCodeListResponse(items=items, total=len(items))


@router.post(
    "/users/invites",
    response_model=InviteCodeSchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def create_invite_code(
    payload: InviteCodeCreateRequest,
    actor: Annotated[AuthenticatedUser, Depends(require_admin_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> InviteCodeSchema:
    """
    管理员生成新的注册邀请码（用于仅限邀请制注册模式）。

    @param payload: 邀请码创建参数
    @param actor: 当前操作管理员
    @param auth_service: 认证服务
    @return: InviteCodeSchema 新建邀请码模型
    """
    try:
        created = await auth_service.create_invite(
            actor_user_id=actor.id,
            role=payload.role,
            max_uses=payload.max_uses,
            expires_in_hours=payload.expires_in_hours,
            note=payload.note,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return InviteCodeSchema(**created)


@router.put(
    "/users/invites/{invite_id}",
    response_model=InviteCodeSchema,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def toggle_invite_code(
    invite_id: int,
    payload: InviteCodeToggleRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> InviteCodeSchema:
    """
    管理员启用或禁用指定邀请码。

    @param invite_id: 邀请码 ID
    @param payload: 目标启用状态
    @param auth_service: 认证服务
    @return: 更新后的 InviteCodeSchema 模型
    """
    try:
        updated = await auth_service.set_invite_active(invite_id, payload.is_active)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return InviteCodeSchema(**updated)


@router.delete(
    "/users/invites/{invite_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def delete_invite_code(
    invite_id: int,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> DeleteResponse:
    """
    管理员永久删除指定邀请码记录。

    @param invite_id: 邀请码 ID
    @param auth_service: 认证服务
    @return: DeleteResponse 删除结果模型
    """
    try:
        await auth_service.delete_invite(invite_id)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return DeleteResponse(deleted=True, id=str(invite_id))


@router.put(
    "/users/{user_id}",
    response_model=AuthUserSchema,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    actor: Annotated[AuthenticatedUser, Depends(require_admin_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    管理员修改指定用户信息、角色、启用状态或重置密码。

    @param user_id: 目标用户 ID
    @param payload: 更新请求体
    @param actor: 当前操作管理员
    @param auth_service: 认证服务
    @return: 更新后的 AuthUserSchema 模型
    """
    try:
        updated = await auth_service.update_user(
            actor_user_id=actor.id,
            target_user_id=user_id,
            display_name=payload.display_name,
            email=payload.email,
            role=payload.role,
            is_active=payload.is_active,
            new_password=payload.new_password,
        )
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return AuthUserSchema(**updated)


@router.post(
    "/users/{user_id}/reset-otp",
    response_model=AuthUserSchema,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def reset_user_otp(
    user_id: int,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthUserSchema:
    """
    管理员强制清除指定用户的 OTP 二步验证配置。

    @param user_id: 目标用户 ID
    @param auth_service: 认证服务
    @return: 更新后的 AuthUserSchema 模型
    """
    try:
        updated = await auth_service.reset_user_otp(user_id)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return AuthUserSchema(**updated)


@router.delete(
    "/users/{user_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(require_admin_user)],
    tags=["Users"],
)
async def delete_user(
    user_id: int,
    actor: Annotated[AuthenticatedUser, Depends(require_admin_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> DeleteResponse:
    """
    管理员删除指定用户账号。

    @param user_id: 目标用户 ID
    @param actor: 当前操作管理员
    @param auth_service: 认证服务
    @return: DeleteResponse 确认模型
    """
    try:
        await auth_service.delete_user(actor_user_id=actor.id, target_user_id=user_id)
    except AuthError as exc:
        _raise_auth_http_error(exc)
    return DeleteResponse(deleted=True, id=str(user_id))
