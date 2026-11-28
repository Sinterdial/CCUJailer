"""
RESTful API Pydantic 请求体与响应体强类型 Schema 定义模块。

所有接口严禁直接返回裸字典，且所有字段必须带有中文 Field(description="...") 说明。
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from cj import APP_VERSION, GITHUB_REPO_URL
from cj.core.config import VALID_STORE_FILTER_KEYS, VALID_STORE_SORT_KEYS


class UserPreferencesSchema(BaseModel):
    """
    已登录用户跨设备同步的个人网站偏好配置模型。
    """

    language_mode: Literal["bilingual", "zh", "en"] | None = Field(
        default=None, description="船名及厂商显示语言偏好"
    )
    theme_mode: Literal["system", "light", "dark"] | None = Field(
        default=None, description="界面外观主题偏好"
    )
    auec_rate: int | None = Field(
        default=None, ge=1000, le=100000000, description="每小时预估游戏内收入（aUEC/h）"
    )
    details_card_order: list[str] | None = Field(
        default=None, description="舰船详情弹窗信息卡片自定义排序"
    )
    details_collapsed_cards: list[str] | None = Field(
        default=None, description="舰船详情弹窗默认折叠的信息卡片列表"
    )
    webhook_card_order: list[str] | None = Field(
        default=None, description="推送渠道设置页卡片自定义排序"
    )
    webhook_collapsed_ids: list[str] | None = Field(
        default=None, description="推送渠道设置页默认折叠的规则 ID 列表"
    )
    owned_ccus: list[list[int]] | None = Field(
        default=None, description="升级路线规划器中已提前囤好的 CCU [from_ref, to_ref] 列表"
    )
    default_store_filters: list[str] | None = Field(
        default=None,
        description="个人首页默认筛选键列表（null 表示跟随系统全局默认设置）",
    )
    default_store_sort: str | None = Field(
        default=None,
        description="个人首页默认排序键（null 表示跟随系统全局默认设置）",
    )
    store_page_size: int | None = Field(
        default=None,
        ge=0,
        le=500,
        description="舰船列表每页最大显示数量（0 表示显示全部，null 表示使用系统默认）",
    )


class UserPreferencesUpdateRequest(BaseModel):
    """
    更新当前登录用户个人偏好配置请求模型（支持按字段局部热更新）。
    """

    language_mode: Literal["bilingual", "zh", "en"] | None = Field(
        default=None, description="船名及厂商显示语言偏好"
    )
    theme_mode: Literal["system", "light", "dark"] | None = Field(
        default=None, description="界面外观主题偏好"
    )
    auec_rate: int | None = Field(
        default=None, ge=1000, le=100000000, description="每小时预估游戏内收入（aUEC/h）"
    )
    details_card_order: list[str] | None = Field(
        default=None, description="舰船详情弹窗信息卡片排序"
    )
    details_collapsed_cards: list[str] | None = Field(
        default=None, description="舰船详情弹窗折叠卡片列表"
    )
    webhook_card_order: list[str] | None = Field(
        default=None, description="推送渠道设置页卡片排序"
    )
    webhook_collapsed_ids: list[str] | None = Field(
        default=None, description="推送渠道设置页折叠卡片 ID 列表"
    )
    owned_ccus: list[list[int]] | None = Field(
        default=None, description="升级路线规划器中已囤好的 CCU 列表"
    )
    default_store_filters: list[str] | None = Field(
        default=None,
        description="个人首页默认筛选键列表；传入 null 表示恢复跟随系统默认",
    )
    default_store_sort: str | None = Field(
        default=None,
        description="个人首页默认排序键；传入 null 表示恢复跟随系统默认",
    )
    store_page_size: int | None = Field(
        default=None,
        ge=0,
        le=500,
        description="舰船列表每页最大显示数量（0 表示显示全部）",
    )

    @field_validator("default_store_filters")
    @classmethod
    def _validate_pref_filters(cls, value: list[str] | None) -> list[str] | None:
        """
        校验个人默认首页筛选键列表均属于系统合法筛选项。

        @param value: 筛选键列表或 None
        @return: 去重校验后的筛选键列表或 None
        @raise ValueError: 包含未知筛选项时抛出
        """
        if value is None:
            return None
        valid = set(VALID_STORE_FILTER_KEYS)
        cleaned: list[str] = []
        for item in value:
            key = str(item).strip()
            if key not in valid:
                raise ValueError(f"不支持的默认筛选条件：{key}")
            if key not in cleaned:
                cleaned.append(key)
        return cleaned

    @field_validator("default_store_sort")
    @classmethod
    def _validate_pref_sort(cls, value: str | None) -> str | None:
        """
        校验个人默认排序键是否合法。

        @param value: 排序键或 None
        @return: 合法排序键或 None
        @raise ValueError: 排序键不被支持时抛出
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned not in VALID_STORE_SORT_KEYS:
            raise ValueError(f"不支持的默认排序方式：{cleaned}")
        return cleaned


class PasskeyItemSchema(BaseModel):
    """
    用户已绑定的 Passkey 通行密钥摘要模型。
    """

    id: int = Field(description="Passkey 凭据内部唯一 ID")
    name: str = Field(description="设备或密钥显示名称")
    transports: list[str] = Field(default_factory=list, description="支持的传输方式列表")
    backed_up: bool = Field(description="该通行密钥是否支持跨设备云端备份同步")
    last_used_at: datetime | None = Field(default=None, description="最近一次用于登录的 UTC 时间")
    created_at: datetime = Field(description="绑定创建 UTC 时间")


class PasskeyListResponse(BaseModel):
    """
    当前用户已绑定 Passkey 列表响应模型。
    """

    items: list[PasskeyItemSchema] = Field(description="已绑定 Passkey 凭据列表")
    total: int = Field(description="Passkey 总数")


class PasskeyChallengeResponse(BaseModel):
    """
    发起 Passkey 注册或认证仪式返回的挑战参数响应模型。
    """

    challenge_id: str = Field(description="服务端单次挑战关联标识")
    options: dict[str, Any] = Field(
        description="标准 WebAuthn CreationOptions / RequestOptions JSON"
    )


class PasskeyRegisterVerifyRequest(BaseModel):
    """
    提交浏览器生成的 Passkey 注册凭据响应模型。
    """

    challenge_id: str = Field(min_length=8, description="注册挑战标识 ID")
    name: str = Field(default="", max_length=120, description="用户为该通行密钥指定的名称")
    credential: dict[str, Any] = Field(
        description="navigator.credentials.create() 返回的 JSON 凭据"
    )


class PasskeyLoginOptionsRequest(BaseModel):
    """
    发起 Passkey 免密或指定账号登录请求模型。
    """

    username: str = Field(
        default="",
        max_length=64,
        description="可选用户名提示（留空启用可发现凭据）",
    )


class PasskeyLoginVerifyRequest(BaseModel):
    """
    提交浏览器生成的 Passkey 登录签名响应模型。
    """

    challenge_id: str = Field(min_length=8, description="认证挑战标识 ID")
    credential: dict[str, Any] = Field(
        description="navigator.credentials.get() 返回的 JSON 断言凭据"
    )


class PasskeyRenameRequest(BaseModel):
    """
    重命名已绑定 Passkey 设备名称请求模型。
    """

    name: str = Field(min_length=1, max_length=120, description="新的 Passkey 设备显示名称")


class HealthResponse(BaseModel):
    """
    服务健康检查响应模型。
    """

    status: str = Field(description="服务运行状态，健康时返回 ok")


class AuthUserSchema(BaseModel):
    """
    已登录及受管用户的公开信息模型（不包含密码哈希或 OTP 密钥）。
    """

    id: int = Field(description="用户内部唯一主键 ID")
    username: str = Field(description="登录用户名")
    display_name: str = Field(description="界面显示昵称")
    email: str = Field(description="电子邮箱")
    role: Literal["admin", "user"] = Field(description="账号角色（admin 管理员 / user 普通用户）")
    is_active: bool = Field(description="账号是否处于启用状态")
    totp_enabled: bool = Field(description="是否已开启 TOTP 动态口令二步验证")
    totp_backup_codes_remaining: int = Field(
        default=0, description="当前剩余可用的 TOTP 一次性备用恢复码数量"
    )
    has_password: bool = Field(description="是否已配置本地密码")
    oidc_bound: bool = Field(description="是否已绑定外部 OIDC SSO 身份")
    passkey_count: int = Field(default=0, description="已绑定的 Passkey 通行密钥数量")
    preferences: UserPreferencesSchema = Field(
        default_factory=UserPreferencesSchema, description="跨设备同步的个人网站偏好"
    )
    last_login_at: datetime | None = Field(default=None, description="最近一次成功登录 UTC 时间")
    created_at: datetime | None = Field(default=None, description="账号创建 UTC 时间")
    updated_at: datetime | None = Field(default=None, description="账号最后更新 UTC 时间")


class AuthStatusResponse(BaseModel):
    """
    系统初始化与当前会话认证状态响应模型。
    """

    initialized: bool = Field(description="系统是否已完成首个管理员账号初始化")
    auth_required: bool = Field(description="系统当前是否开启全站强制登录")
    registration_mode: Literal["open", "invite_only", "closed"] = Field(
        default="closed", description="当前用户自助注册策略（open/invite_only/closed）"
    )
    authenticated: bool = Field(description="当前请求是否已成功认证登录")
    sso_enabled: bool = Field(description="是否已启用并配置好 OIDC SSO 登录")
    sso_provider_name: str = Field(description="SSO 认证提供方名称（如 ZITADEL）")
    passkey_enabled: bool = Field(default=True, description="是否已启用 Passkey 通行密钥登录")
    default_store_filters: list[str] = Field(
        default_factory=list, description="系统全局首页默认筛选键列表"
    )
    default_store_sort: str = Field(
        default="price-desc", description="系统全局首页默认排序键"
    )
    upgrade_planner_enabled: bool = Field(
        default=False, description="是否已启用升级路线规划（/plan）功能"
    )
    app_version: str = Field(default=APP_VERSION, description="当前后端服务版本号")
    github_url: str = Field(default=GITHUB_REPO_URL, description="项目 GitHub 主页地址")
    user: AuthUserSchema | None = Field(
        default=None, description="当前已登录用户详情，未登录时为 null"
    )


class SetupAdminRequest(BaseModel):
    """
    首次启动网页引导创建初始管理员请求模型。
    """

    username: str = Field(min_length=2, max_length=64, description="初始管理员用户名")
    password: str = Field(min_length=8, max_length=128, description="初始管理员密码（不少于 8 位）")
    display_name: str = Field(default="", max_length=120, description="可选管理员显示昵称")


class LoginRequest(BaseModel):
    """
    账号密码登录请求模型。
    """

    username: str = Field(min_length=1, max_length=64, description="用户名")
    password: str = Field(min_length=1, max_length=128, description="密码")


class LoginOtpRequest(BaseModel):
    """
    二步验证 TOTP 动态口令或一次性备用恢复码提交请求模型。
    """

    mfa_token: str = Field(min_length=10, description="密码登录返回的短期 MFA 挑战令牌")
    otp_code: str = Field(
        min_length=6, max_length=20, description="6 位 TOTP 动态口令或一次性备用恢复码"
    )


class LoginResponse(BaseModel):
    """
    登录或初始化完成后返回的认证结果模型。
    """

    mfa_required: bool = Field(default=False, description="是否需要进一步输入 OTP 验证码")
    mfa_token: str | None = Field(default=None, description="需要 OTP 验证时签发的短期挑战令牌")
    access_token: str | None = Field(default=None, description="认证成功后签发的会话 Bearer JWT")
    expires_at: datetime | None = Field(default=None, description="会话过期 UTC 时间")
    user: AuthUserSchema | None = Field(default=None, description="当前登录用户摘要")


class ChangePasswordRequest(BaseModel):
    """
    当前用户修改自身登录密码请求模型。
    """

    current_password: str = Field(
        default="", max_length=128, description="原密码（纯 SSO 初次设密可留空）"
    )
    new_password: str = Field(min_length=8, max_length=128, description="新密码（至少 8 位）")


class TotpSetupResponse(BaseModel):
    """
    发起 TOTP 绑定时返回的密钥与内联 SVG 二维码响应模型。
    """

    secret: str = Field(description="Base32 明文密钥（仅在绑定引导期展示供手动输入）")
    otpauth_uri: str = Field(description="标准 otpauth:// 配对 URI")
    qr_svg: str = Field(description="本地生成的内联 SVG 二维码图形字符串")


class TotpVerifyRequest(BaseModel):
    """
    确认开启 TOTP 动态口令请求模型。
    """

    otp_code: str = Field(min_length=6, max_length=12, description="验证器 App 生成的 6 位动态口令")


class TotpEnableResponse(BaseModel):
    """
    启用 TOTP 二步验证成功后返回的用户状态与仅展示一次的备用恢复码响应模型。
    """

    user: AuthUserSchema = Field(description="更新后的当前登录用户摘要")
    backup_codes: list[str] = Field(
        default_factory=list, description="仅在本次生成的 8 个一次性备用恢复码"
    )


class TotpBackupCodesRegenerateRequest(BaseModel):
    """
    重新生成 TOTP 一次性备用恢复码验证请求模型。
    """

    otp_code: str = Field(default="", max_length=20, description="当前 6 位 OTP 动态口令（优先）")
    current_password: str = Field(default="", max_length=128, description="或当前登录密码")


class TotpBackupCodesResponse(BaseModel):
    """
    重新生成备用恢复码响应模型。
    """

    backup_codes: list[str] = Field(description="新生成的 8 个一次性备用恢复码")
    remaining: int = Field(description="当前有效备用恢复码总数量")


class TotpDisableRequest(BaseModel):
    """
    停用自身 TOTP 二步验证请求模型。
    """

    otp_code: str = Field(default="", max_length=20, description="当前 6 位 OTP 口令或备用恢复码")
    current_password: str = Field(default="", max_length=128, description="或输入当前账号密码核验")


class SsoAuthorizeResponse(BaseModel):
    """
    获取 OIDC PKCE 授权跳转地址响应模型。
    """

    authorize_url: str = Field(description="附带 PKCE S256 挑战与防伪 state 的 SSO 授权页完整 URL")


class SsoCallbackRequest(BaseModel):
    """
    前端回传 ZITADEL OIDC 授权码换票请求模型。
    """

    code: str = Field(min_length=1, description="授权服务器回调的 Authorization Code")
    state: str = Field(min_length=1, description="回传的防伪状态字符串")
    redirect_uri: str = Field(min_length=8, description="本次认证使用的完整前端回调 URL")


class UserCreateRequest(BaseModel):
    """
    管理员新建用户请求模型。
    """

    username: str = Field(min_length=2, max_length=64, description="用户名")
    password: str = Field(min_length=8, max_length=128, description="初始密码（至少 8 位）")
    display_name: str = Field(default="", max_length=120, description="显示昵称")
    email: str = Field(default="", max_length=255, description="电子邮箱")
    role: Literal["admin", "user"] = Field(default="user", description="分配角色")
    is_active: bool = Field(default=True, description="创建后是否立即启用")


class UserUpdateRequest(BaseModel):
    """
    管理员编辑用户资料、权限与启用状态请求模型。
    """

    display_name: str | None = Field(default=None, max_length=120, description="显示昵称")
    email: str | None = Field(default=None, max_length=255, description="电子邮箱")
    role: Literal["admin", "user"] | None = Field(default=None, description="变更角色")
    is_active: bool | None = Field(default=None, description="变更启用状态")
    new_password: str | None = Field(
        default=None, min_length=8, max_length=128, description="可选重置登录密码"
    )


class UserListResponse(BaseModel):
    """
    用户管理列表响应模型。
    """

    items: list[AuthUserSchema] = Field(description="系统全部用户摘要列表")
    total: int = Field(description="用户总数")


class RegisterUserRequest(BaseModel):
    """
    用户自助注册请求模型（开放注册或邀请制注册模式使用）。
    """

    username: str = Field(min_length=2, max_length=64, description="用户名")
    password: str = Field(min_length=8, max_length=128, description="登录密码（至少 8 位）")
    display_name: str = Field(default="", max_length=120, description="显示昵称")
    email: str = Field(default="", max_length=255, description="电子邮箱")
    invite_code: str = Field(default="", max_length=64, description="邀请码（邀请制必填）")


class InviteCodeSchema(BaseModel):
    """
    注册邀请码摘要模型。
    """

    id: int = Field(description="邀请码内部唯一 ID")
    code: str = Field(description="邀请码字符串（如 CJ-XXXX-XXXX-XXXX）")
    role: Literal["admin", "user"] = Field(description="通过该邀请码注册后分配的角色")
    note: str = Field(description="管理员备注标签")
    max_uses: int = Field(description="最大可用次数上限")
    used_count: int = Field(description="已使用次数")
    is_active: bool = Field(description="是否处于启用状态")
    expires_at: datetime | None = Field(default=None, description="过期 UTC 时间（null 永不过期）")
    created_at: datetime = Field(description="创建时间（UTC）")
    last_used_at: datetime | None = Field(default=None, description="最近一次被使用 UTC 时间")


class InviteCodeListResponse(BaseModel):
    """
    邀请码管理列表响应模型。
    """

    items: list[InviteCodeSchema] = Field(description="系统全部邀请码记录")
    total: int = Field(description="邀请码总数")


class InviteCodeCreateRequest(BaseModel):
    """
    创建注册邀请码请求模型。
    """

    role: Literal["admin", "user"] = Field(default="user", description="分配角色")
    max_uses: int = Field(default=1, ge=1, le=1000, description="最大使用次数（1-1000）")
    expires_in_hours: int | None = Field(
        default=168, ge=1, le=87600, description="有效时长（小时，null 表示永不过期）"
    )
    note: str = Field(default="", max_length=200, description="可选备注说明")


class InviteCodeToggleRequest(BaseModel):
    """
    切换邀请码启用状态请求模型。
    """

    is_active: bool = Field(description="是否启用该邀请码")


class StoreEditionSchema(BaseModel):
    """
    单船在售或历史版本（Standard / Warbond / 10 Year / Best in Show）条目模型。
    """

    label: str = Field(description="版本分类标签")
    price: int = Field(description="当前销售价格（美分）")
    list_price: int | None = Field(default=None, description="划线原始标价（美分）")
    in_store: bool = Field(description="当前是否在商店公开上架")
    available: bool = Field(description="当前是否有库存可下单")
    limited: bool = Field(description="是否限量版商品")
    level: str = Field(description="库存紧张程度提示")
    url: str = Field(description="RSI 商店购买页面直达链接")
    since: datetime | None = Field(default=None, description="本轮上架开始 UTC 时间")
    last_seen: datetime | None = Field(default=None, description="最近一次探测到在售时间")
    insurance: str = Field(description="随附保险期限说明（如 Lifetime / 10 years）")
    paints: int = Field(default=0, description="附带涂装数量")
    includes: list[str] = Field(default_factory=list, description="随附赠品摘要列表")
    modules: list[str] = Field(default_factory=list, description="套装包含的舰船模块列表")


class StoreModuleSchema(BaseModel):
    """
    Add-Ons 商店单独售卖的舰船模块条目模型。
    """

    name: str = Field(description="模块名称")
    price: int = Field(description="模块价格（美分）")
    list_price: int | None = Field(default=None, description="划线原价（美分）")
    url: str = Field(description="模块商品购买链接")
    available: bool = Field(description="是否有货")
    insurance: str = Field(description="模块保险说明")
    label: str = Field(description="版本标签")


class NearestFromShipSchema(BaseModel):
    """
    最近可升级起始底船推荐项模型。
    """

    name_en: str = Field(description="起始底船英文标准名")
    name_zh: str = Field(description="起始底船中文标准名")
    value: float = Field(description="起始底船当前估值（USD）")
    upgrade_cost: float = Field(description="从该船升级至目标 CCU 所需补齐差价（USD）")
    always_available: bool = Field(description="该起始底船是否在 RSI 商店常驻无限制可买")


class StoreUpgradeOptionSchema(BaseModel):
    """
    目标舰船在售升级包（CCU）摘要模型。
    """

    sku: int = Field(description="RSI 升级 SKU ID")
    label: str = Field(description="精简版本标签")
    price: int = Field(description="升级目标估值（美分）")
    is_warbond: bool = Field(description="是否属于 Warbond 优惠升级")
    expires_at: datetime | None = Field(
        default=None, description="到期 UTC 时间（未公布时为 null）"
    )
    nearest_from_ships: list[NearestFromShipSchema] = Field(
        default_factory=list, description="距离该 CCU 最近的可用起始底船列表"
    )


class StoreShipSchema(BaseModel):
    """
    全量舰船及原版 PledgeWatch 富化视图单行数据模型。
    """

    id: int = Field(description="内部舰船主键 ID")
    key: str = Field(description="归一化船名检索键")
    name_en: str = Field(description="英文标准船名")
    name_zh: str = Field(description="中文标准译名")
    maker: str = Field(description="制造商名称")
    maker_code: str = Field(description="制造商代码")
    role: str = Field(description="舰船角色定位")
    type: str = Field(description="舰船类型")
    size: str = Field(description="舰船体型尺寸")
    crew: str = Field(description="乘员数量或范围")
    cargo: float | None = Field(default=None, description="货舱容积（SCU）")
    length: float | None = Field(default=None, description="舰船长度（米）")
    status: str = Field(description="飞行就绪状态（Flight ready / In concept 等）")
    loaners: str = Field(description="官方借用船说明")
    released: str = Field(description="首次加入游戏的补丁版本号")
    sold: str = Field(description="Wiki 记录的售卖规律（always / time / quantity / retiring）")
    gold: dict[str, Any] | None = Field(default=None, description="金牌标准（Gold Standard）进度")
    img: str = Field(description="舰船缩略图 URL")
    rsi: str = Field(description="RSI 官方商店或百科页面链接")
    always_available: bool = Field(description="是否常驻无限制直购")
    in_store: bool = Field(description="当前在单船商店或可直购列表中是否上架")
    editions: list[StoreEditionSchema] = Field(description="关联的单船购买版本集合")
    modules: list[StoreModuleSchema] = Field(description="关联的在售模块列表")
    ccu: dict[str, int] | None = Field(default=None, description="升级基准 ref ID 与估值美分")
    upgrades: list[StoreUpgradeOptionSchema] = Field(description="当前在售的升级 CCU 选项")
    insurance: dict[str, Any] | None = Field(default=None, description="游戏内索赔与加急时间详情")
    buy: list[list[Any]] = Field(default_factory=list, description="游戏内购买价格与商店地点")
    rent: list[list[Any]] = Field(default_factory=list, description="游戏内租赁价格与终端地点")
    weapons: dict[str, Any] | None = Field(
        default=None, description="原厂武器配置与主驾/炮手 DPS 汇总"
    )
    class_name: str = Field(default="", description="游戏内载具类名称")
    history: list[list[Any]] = Field(default_factory=list, description="历史直购与 CCU 价格轨迹")
    grid: dict[str, Any] | None = Field(default=None, description="默认俯视货舱网格数据")
    module_grids: dict[str, Any] = Field(
        default_factory=dict, description="装配模块化舱室后的货舱网格集合"
    )


class FullStoreResponse(BaseModel):
    """
    商店总览页完整数据集响应模型。
    """

    ships: list[StoreShipSchema] = Field(description="全量舰船数据及版本/游戏内/货舱聚合列表")
    changes: list[ChangelogItemResponse] = Field(description="近期商店与 CCU 变动历史记录")
    game_version: str = Field(description="数据标注的星际公民游戏版本号")
    tracking_since: datetime | None = Field(default=None, description="系统开始监控记录的起始时间")
    checked_at: datetime | None = Field(default=None, description="最近一次商店检查时间")


class UpgradeCostsResponse(BaseModel):
    """
    指定起始船实时升级费用查询响应模型。
    """

    ref: int = Field(description="起始底船 RSI upgrade ID")
    at: int = Field(description="查询缓存 Unix 时间戳（秒）")
    costs: dict[str, int] = Field(description="可升级目标 CCU SKU ID 到差价（美分）映射")


class UpgradePlanResponse(BaseModel):
    """
    升级路线规划器（/plan）有向图响应模型。
    """

    at: str | None = Field(default=None, description="最近一次真实路径抓图 UTC ISO 时间")
    estimated: bool = Field(description="若后台尚未跑完首轮路径探测则为 true，使用标价差估算边")
    ships: list[list[Any]] = Field(
        description="节点列表：[ref_id, name_en, name_zh, value_cents, in_store, always]"
    )
    skus: dict[str, list[Any]] = Field(
        description="在售 CCU 边元数据：{sku_id: [target_ref, label, price_cents, is_wb]}"
    )
    paths: dict[str, list[list[int]]] = Field(
        description="邻接表：{from_ref_str: [[sku_id, cost_cents], ...]}"
    )


class ShipI18nOverrideRequest(BaseModel):
    """
    手动设置或清除单艘舰船中文译名覆盖请求模型。
    """

    name_zh: str = Field(
        max_length=160,
        description="自定义中文译名；传空串表示撤销人工覆盖并恢复自动词典",
    )


class ShipI18nOverrideResponse(BaseModel):
    """
    手动覆盖单船中文译名结果响应模型。
    """

    id: int = Field(description="舰船主键 ID")
    name_en: str = Field(description="英文船名")
    name_zh: str = Field(description="更新后生效的中文译名")
    auto_translated: bool = Field(description="是否标记为 AI 自动翻译")


class StatusSummaryResponse(BaseModel):
    """
    首页看板统计概览响应模型。
    """

    total_listed_ccus: int = Field(description="当前在售 CCU SKU 总数")
    warbond_ccus: int = Field(description="当前在售 Warbond 优惠 CCU 数量")
    total_ships: int = Field(description="系统收录星际公民舰船总数")
    translated_ships: int = Field(description="已完成中文译名映射的舰船数量")
    last_scrape_at: datetime | None = Field(
        default=None,
        description="最近一次成功同步 RSI 商店的 UTC 时间",
    )


class CcuItemResponse(BaseModel):
    """
    单个在售 CCU 详情响应模型。
    """

    sku_id: int = Field(description="RSI 升级 SKU 唯一编号")
    ship_id: int = Field(description="内部舰船主键 ID")
    rsi_upgrade_id: int | None = Field(default=None, description="RSI 升级工具船只 ID")
    ship_name_en: str = Field(description="目标舰船英文标准名")
    ship_name_zh: str = Field(description="目标舰船中文标准名")
    manufacturer: str = Field(description="舰船制造商名称")
    title: str = Field(description="RSI 原始升级版本标题")
    edition_label: str = Field(description="精简版本标签（如 Warbond / Standard）")
    is_warbond: bool = Field(description="是否为 Warbond 新钱优惠版本")
    original_price: float = Field(description="目标舰船常规原价（USD）")
    discounted_price: float = Field(description="当前升级购买价（USD）")
    savings: float = Field(description="相比原价可节省金额（USD）")
    currency: str = Field(description="计价货币代码，固定为 USD")
    expires_at: datetime | None = Field(
        default=None,
        description="预计下架或优惠到期 UTC 时间；RSI 未公布时为 null",
    )
    store_url: str = Field(description="RSI 商店对应页面直达链接")
    image_url: str = Field(description="目标舰船缩略图 URL")
    listed_since_at: datetime = Field(description="本轮上架开始监控的 UTC 时间")
    last_seen_at: datetime = Field(description="最近一次探测到在售状态的 UTC 时间")
    nearest_from_ships: list[NearestFromShipSchema] = Field(
        description="距离目标价最近的可用起始底船推荐列表（最多 5 艘）"
    )


class CcuListResponse(BaseModel):
    """
    在售 CCU 列表响应模型。
    """

    items: list[CcuItemResponse] = Field(description="符合筛选条件的在售 CCU 列表")
    total: int = Field(description="返回的 CCU 条目总数")


class CcuRefreshResponse(BaseModel):
    """
    手动触发 CCU 同步校验结果响应模型。
    """

    status: str = Field(description="本次刷新执行状态（ok/skipped/error）")
    baseline: bool = Field(default=False, description="是否为首次冷启动静默基线捕获")
    seen_skus: int = Field(default=0, description="本次抓到的在售 CCU SKU 数量")
    listed: int = Field(default=0, description="本次新上架 CCU 数量")
    relisted: int = Field(default=0, description="本次重新返场 CCU 数量")
    price_changed: int = Field(default=0, description="本次发生价格变动的 CCU 数量")
    delisted: int = Field(default=0, description="本次连续缺失确认下架的 CCU 数量")
    published_events: int = Field(default=0, description="提交后派发至事件总线的通知事件数")
    reason: str = Field(default="", description="异常或跳过时的原因说明")


class ShipItemResponse(BaseModel):
    """
    全量舰船与中英双语映射条目模型。
    """

    id: int = Field(description="内部舰船 ID")
    key: str = Field(description="归一化检索键")
    rsi_upgrade_id: int | None = Field(default=None, description="RSI 升级工具船只 ID")
    name_en: str = Field(description="英文标准船名")
    name_zh: str = Field(description="中文标准译名")
    auto_translated: bool = Field(description="中文译名是否由 AI 兜底自动生成")
    manufacturer: str = Field(description="制造商名称")
    focus: str = Field(description="舰船定位")
    size: str = Field(description="舰船尺寸级别")
    production_status: str = Field(description="当前开发与飞行就绪状态")
    value: float = Field(description="升级估值（USD）")
    always_available: bool = Field(description="是否常驻无限制可买")
    in_store: bool = Field(description="当前单船商店是否在售")
    store_url: str = Field(description="RSI 商店或舰船详情链接")
    image_url: str = Field(description="舰船缩略图 URL")
    updated_at: datetime = Field(description="记录最后更新 UTC 时间")


class ShipListResponse(BaseModel):
    """
    舰船清单响应模型。
    """

    items: list[ShipItemResponse] = Field(description="全量舰船及译名列表")
    total: int = Field(description="符合条件的舰船总数")


class ChangelogItemResponse(BaseModel):
    """
    单条 CCU 历史上下架与调价记录模型。
    """

    id: int = Field(description="日志自增主键 ID")
    occurred_at: datetime = Field(description="变动发生 UTC 时间")
    event_type: str = Field(
        description="事件类型（ccu.listed/ccu.relisted/ccu.price_changed/ccu.delisted）"
    )
    sku_id: int = Field(description="关联的 CCU SKU ID")
    ship_name_en: str = Field(description="舰船英文名")
    ship_name_zh: str = Field(description="舰船中文名")
    edition_label: str = Field(description="CCU 版本标签")
    is_warbond: bool = Field(description="是否为 Warbond 版本")
    old_price: float | None = Field(default=None, description="变更前价格（USD）")
    new_price: float | None = Field(default=None, description="变更后价格（USD）")
    original_price: float | None = Field(default=None, description="舰船原价（USD）")
    details: dict[str, Any] = Field(default_factory=dict, description="附加结构化详情")


class ChangelogListResponse(BaseModel):
    """
    历史变动日志分页响应模型。
    """

    items: list[ChangelogItemResponse] = Field(description="变动历史记录列表")
    total: int = Field(description="符合条件的总记录数")


class WebhookFilterSchema(BaseModel):
    """
    Webhook 规则过滤条件模型（所有非空条件按 AND 关系求交集）。
    """

    only_warbond: bool = Field(default=False, description="为 true 时仅匹配 Warbond 优惠 CCU")
    ship_keywords: list[str] = Field(
        default_factory=list,
        description="中英文船名关键词列表，内部按 OR 逻辑模糊匹配；为空表示不限制",
    )
    min_original_price: float | None = Field(
        default=None, ge=0, description="目标舰船最低原价（USD），null 表示不限制"
    )
    max_original_price: float | None = Field(
        default=None, ge=0, description="目标舰船最高原价（USD），null 表示不限制"
    )
    min_wb_price: float | None = Field(
        default=None, ge=0, description="实际购买价（优惠价）下限（USD），null 表示不限制"
    )
    max_wb_price: float | None = Field(
        default=None, ge=0, description="实际购买价（优惠价）上限（USD），null 表示不限制"
    )
    min_savings: float | None = Field(
        default=None, ge=0, description="最低优惠立省金额（USD），null 表示不限制"
    )


WebhookProviderLiteral = Literal["generic", "feishu", "wecom", "discord", "custom"]
WebhookMethodLiteral = Literal["POST", "PUT", "PATCH"]
VALID_WEBHOOK_EVENTS = {
    "ccu.warbond_upgrade",
    "store.warbond_ship",
    "store.special_sale",
    "ccu.new_listing",
    "ccu.price_changed",
}


class WebhookRuleCreateRequest(BaseModel):
    """
    创建 Webhook 通知规则请求模型。
    """

    id: str | None = Field(
        default=None, max_length=64, description="可选自定义规则 ID；留空由系统自动生成"
    )
    name: str = Field(min_length=1, max_length=120, description="规则显示名称")
    enabled: bool = Field(default=True, description="是否启用该规则")
    url: str = Field(min_length=8, max_length=1024, description="Webhook 目标 HTTP/HTTPS 地址")
    method: WebhookMethodLiteral = Field(default="POST", description="HTTP 请求方法")
    provider_type: WebhookProviderLiteral = Field(
        default="generic",
        description="消息载荷格式（generic/feishu/wecom/discord/custom）",
    )
    custom_template: str | None = Field(
        default=None,
        description="当 provider_type 为 custom 时使用的自定义 JSON 模板字符串",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="附加自定义 HTTP 请求头（敏感字段入库后接口响应将自动脱敏）",
    )
    events: list[str] = Field(
        default_factory=lambda: ["ccu.new_listing", "ccu.price_changed"],
        description=(
            "订阅触发推送的事件列表（ccu.warbond_upgrade / store.warbond_ship / "
            "store.special_sale / ccu.new_listing / ccu.price_changed）"
        ),
    )
    filters: WebhookFilterSchema = Field(
        default_factory=WebhookFilterSchema,
        description="CCU 匹配过滤条件",
    )

    @field_validator("events")
    @classmethod
    def _validate_events(cls, value: list[str]) -> list[str]:
        """
        校验订阅事件列表非空且仅包含系统支持的合法事件标识。

        @param value: 原始事件标识列表
        @return: 去重保序后的事件列表
        @raise ValueError: 当列表为空或包含未定义事件标识时抛出
        """
        cleaned: list[str] = []
        for item in value:
            ev = str(item).strip()
            if ev not in VALID_WEBHOOK_EVENTS:
                raise ValueError(f"不支持的通知事件类型：{ev}")
            if ev not in cleaned:
                cleaned.append(ev)
        if not cleaned:
            raise ValueError("请至少选择一种通知事件类型")
        return cleaned

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """
        校验目标地址必须为合法的 HTTP 或 HTTPS URL。

        @param value: 原始 URL 字符串
        @return: 清理空白后的 URL 字符串
        @raise ValueError: 当协议不为 http:// 或 https:// 时抛出
        """
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Webhook URL 必须以 http:// 或 https:// 开头")
        return cleaned


class WebhookRuleUpdateRequest(BaseModel):
    """
    更新 Webhook 通知规则请求模型。
    """

    name: str | None = Field(default=None, min_length=1, max_length=120, description="规则显示名称")
    enabled: bool | None = Field(default=None, description="是否启用该规则")
    url: str | None = Field(default=None, min_length=8, max_length=1024, description="Webhook 地址")
    method: WebhookMethodLiteral | None = Field(default=None, description="HTTP 请求方法")
    provider_type: WebhookProviderLiteral | None = Field(default=None, description="消息格式平台")
    custom_template: str | None = Field(default=None, description="自定义 JSON 模板字符串")
    headers: dict[str, str] | None = Field(
        default=None,
        description="附加请求头（传入 ****** 开头的脱敏占位符时自动保留原密钥）",
    )
    events: list[str] | None = Field(default=None, description="订阅的事件类型列表")
    filters: WebhookFilterSchema | None = Field(default=None, description="CCU 匹配过滤条件")

    @field_validator("events")
    @classmethod
    def _validate_optional_events(cls, value: list[str] | None) -> list[str] | None:
        """
        更新规则时校验可选事件类型列表的合法性。

        @param value: 事件标识列表或 None
        @return: 去重保序后的事件列表或 None
        @raise ValueError: 当传入空列表或非法事件标识时抛出
        """
        if value is None:
            return None
        return WebhookRuleCreateRequest._validate_events(value)

    @field_validator("url")
    @classmethod
    def _validate_optional_url(cls, value: str | None) -> str | None:
        """
        当传入 URL 字段时校验其协议合法性。

        @param value: 原始 URL 或 None
        @return: 校验后的 URL 或 None
        @raise ValueError: 当 URL 不以 http:// 或 https:// 开头时抛出
        """
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("Webhook URL 必须以 http:// 或 https:// 开头")
        return cleaned


class WebhookRuleResponse(BaseModel):
    """
    Webhook 通知规则响应模型（敏感 Header 已脱敏）。
    """

    id: str = Field(description="规则唯一标识")
    name: str = Field(description="规则名称")
    enabled: bool = Field(description="是否启用")
    url: str = Field(description="Webhook 推送目标地址")
    method: str = Field(description="HTTP 方法")
    provider_type: str = Field(description="消息格式模板类型")
    custom_template: str | None = Field(default=None, description="自定义 JSON 模板")
    headers: dict[str, str] = Field(description="已脱敏的自定义请求头字典")
    events: list[str] = Field(description="订阅触发的事件类型列表")
    filters: WebhookFilterSchema = Field(description="过滤条件配置")
    created_at: datetime = Field(description="创建时间（UTC）")
    updated_at: datetime = Field(description="最后更新时间（UTC）")


class WebhookRuleListResponse(BaseModel):
    """
    Webhook 规则列表响应模型。
    """

    items: list[WebhookRuleResponse] = Field(description="全部已配置的 Webhook 规则")
    total: int = Field(description="规则总数")


class DeleteResponse(BaseModel):
    """
    通用删除操作结果响应模型。
    """

    deleted: bool = Field(description="是否成功删除")
    id: str = Field(description="被删除的资源 ID")


class WebhookTestResponse(BaseModel):
    """
    发送测试 Webhook 通知结果响应模型。
    """

    rule_id: str = Field(description="被测规则 ID")
    rule_name: str = Field(description="被测规则名称")
    success: bool = Field(description="测试推送是否收到 2xx 成功响应")
    status_code: int | None = Field(default=None, description="目标服务器返回的 HTTP 状态码")
    attempt_count: int = Field(description="本次推送总尝试次数")
    duration_ms: int = Field(description="推送耗时（毫秒）")
    error_message: str = Field(description="脱敏后的错误信息（成功时为空字符串）")


class WebhookLogItemResponse(BaseModel):
    """
    单条 Webhook 推送日志响应模型。
    """

    id: int = Field(description="日志 ID")
    rule_id: str = Field(description="所属规则 ID")
    rule_name: str = Field(description="所属规则名称")
    event_type: str = Field(description="触发事件类型")
    ship_name: str = Field(description="推送的目标舰船名称")
    status_code: int | None = Field(default=None, description="HTTP 响应状态码")
    success: bool = Field(description="推送是否成功")
    attempt_count: int = Field(description="重试总次数")
    duration_ms: int = Field(description="推送总耗时（毫秒）")
    error_message: str = Field(description="脱敏后的异常详情")
    created_at: datetime = Field(description="执行投递的 UTC 时间")


class WebhookLogListResponse(BaseModel):
    """
    Webhook 推送日志分页响应模型。
    """

    items: list[WebhookLogItemResponse] = Field(description="推送日志列表")
    total: int = Field(description="符合条件的总日志条数")


class I18nSyncResponse(BaseModel):
    """
    手动触发船名汉化词典同步结果响应模型。
    """

    remote_entries: int = Field(description="从远程源成功拉取的词典条目数")
    total_ships: int = Field(description="数据库中舰船总数")
    updated_ships: int = Field(description="本次更新中文名或搜索索引的舰船数量")


class DynamicSettingsResponse(BaseModel):
    """
    动态系统配置响应模型。
    """

    scrape_interval_seconds: int = Field(description="CCU 抓取检测间隔秒数")
    catalog_sync_interval_seconds: int = Field(description="舰船目录与词典同步间隔秒数")
    i18n_source_url: str = Field(description="远程汉化词典源 URL（支持 JSON 或 global.ini）")
    default_language: Literal["bilingual", "zh", "en"] = Field(
        description="前端默认船名显示模式（bilingual/zh/en）"
    )
    default_store_filters: list[str] = Field(
        default_factory=list, description="系统全局首页默认筛选条件键列表"
    )
    default_store_sort: str = Field(
        default="price-desc", description="系统全局首页默认排序方式键"
    )
    log_retention_days: int = Field(description="推送日志与变动记录保留天数（0 表示永久保留）")
    translate_enabled: bool = Field(description="是否启用 OpenAI 兼容接口自动翻译兜底")
    translate_api_base: str = Field(description="OpenAI 兼容翻译接口 Base URL")
    translate_model: str = Field(description="自动翻译使用的模型名称")
    translate_api_key_configured: bool = Field(
        description="后端环境变量中是否已配置 TRANSLATE_API_KEY（不暴露明文）"
    )
    auth_require_login: bool = Field(
        description="当前是否开启全站强制登录（只读，仅由环境变量 AUTH_REQUIRE_LOGIN 控制）"
    )
    registration_mode: Literal["open", "invite_only", "closed"] = Field(
        description="新用户注册策略（open/invite_only/closed）"
    )
    sso_enabled: bool = Field(
        description="是否启用 OIDC SSO 登录（支持 ZITADEL PKCE 无 Secret 模式）"
    )
    sso_provider_name: str = Field(description="SSO 提供方显示名称")
    sso_issuer_url: str = Field(description="OIDC Issuer 根地址")
    sso_client_id: str = Field(description="OIDC PKCE Public Client ID")
    sso_scopes: str = Field(description="OIDC 请求 Scope 列表")
    sso_auto_register: bool = Field(description="SSO 首次登录时是否自动创建新账号")
    sso_default_role: Literal["admin", "user"] = Field(description="SSO 自动注册新账号的默认角色")
    passkey_enabled: bool = Field(default=True, description="是否启用 Passkey 通行密钥登录")
    passkey_rp_id: str = Field(default="", description="Passkey RP ID（留空时自动识别访问域名）")
    passkey_rp_name: str = Field(default="CCUJailer", description="Passkey 站点展示名称")
    passkey_origin: str = Field(
        default="", description="Passkey 允许来源 Origin（逗号分隔，留空自动识别）"
    )
    upgrade_planner_enabled: bool = Field(description="是否启用升级路线规划（/plan）功能")


class DynamicSettingsUpdateRequest(BaseModel):
    """
    热更新动态系统配置请求模型。
    """

    scrape_interval_seconds: int | None = Field(
        default=None, ge=30, le=86400, description="CCU 抓取检测间隔（30-86400 秒）"
    )
    catalog_sync_interval_seconds: int | None = Field(
        default=None, ge=300, le=604800, description="目录与词典同步间隔（300-604800 秒）"
    )
    i18n_source_url: str | None = Field(
        default=None, max_length=1024, description="远程汉化词典源地址"
    )
    default_language: Literal["bilingual", "zh", "en"] | None = Field(
        default=None, description="前端默认显示语言模式"
    )
    default_store_filters: list[str] | None = Field(
        default=None, description="系统全局首页默认筛选条件键列表"
    )
    default_store_sort: str | None = Field(
        default=None, description="系统全局首页默认排序方式键"
    )
    log_retention_days: int | None = Field(
        default=None, ge=0, le=3650, description="历史日志保留天数（0-3650）"
    )
    translate_enabled: bool | None = Field(default=None, description="是否开启自动翻译兜底")
    translate_api_base: str | None = Field(
        default=None, max_length=512, description="OpenAI 兼容翻译接口 Base URL"
    )
    translate_model: str | None = Field(
        default=None, max_length=128, description="自动翻译模型名称"
    )
    registration_mode: Literal["open", "invite_only", "closed"] | None = Field(
        default=None, description="新用户注册策略（open/invite_only/closed）"
    )
    sso_enabled: bool | None = Field(default=None, description="是否启用 OIDC SSO 登录")
    sso_provider_name: str | None = Field(
        default=None, max_length=64, description="SSO 提供方显示名称"
    )
    sso_issuer_url: str | None = Field(
        default=None, max_length=512, description="OIDC Issuer 根地址"
    )
    sso_client_id: str | None = Field(default=None, max_length=256, description="OIDC Client ID")
    sso_scopes: str | None = Field(default=None, max_length=256, description="OIDC Scope 列表")
    sso_auto_register: bool | None = Field(
        default=None, description="是否允许 SSO 首次登录自动注册"
    )
    sso_default_role: Literal["admin", "user"] | None = Field(
        default=None, description="SSO 自动注册默认角色"
    )
    passkey_enabled: bool | None = Field(default=None, description="是否启用 Passkey 通行密钥登录")
    passkey_rp_id: str | None = Field(
        default=None, max_length=255, description="Passkey 依赖方域名 RP ID"
    )
    passkey_rp_name: str | None = Field(
        default=None, max_length=120, description="Passkey 站点展示名称"
    )
    passkey_origin: str | None = Field(
        default=None, max_length=512, description="Passkey 允许来源 Origin 列表"
    )
    upgrade_planner_enabled: bool | None = Field(
        default=None, description="是否启用升级路线规划（/plan）功能"
    )

    @field_validator("default_store_filters")
    @classmethod
    def _validate_default_filters(cls, value: list[str] | None) -> list[str] | None:
        """
        校验系统全局默认首页筛选键列表是否合法。

        @param value: 筛选键列表或 None
        @return: 去重后的合法筛选键列表或 None
        @raise ValueError: 当包含不支持的筛选项时抛出
        """
        return UserPreferencesUpdateRequest._validate_pref_filters(value)

    @field_validator("default_store_sort")
    @classmethod
    def _validate_default_sort(cls, value: str | None) -> str | None:
        """
        校验系统全局默认排序方式键是否合法。

        @param value: 排序键或 None
        @return: 合法排序键或 None
        @raise ValueError: 当排序键不被支持时抛出
        """
        return UserPreferencesUpdateRequest._validate_pref_sort(value)


FullStoreResponse.model_rebuild()
