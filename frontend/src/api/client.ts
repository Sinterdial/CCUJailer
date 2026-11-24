import type {
  AuthStatus,
  AuthUser,
  CcuListResponse,
  CcuRefreshResponse,
  ChangelogListResponse,
  DeleteResponse,
  DynamicSettings,
  DynamicSettingsUpdatePayload,
  FullStoreResponse,
  I18nSyncResponse,
  InviteCodeCreatePayload,
  InviteCodeItem,
  InviteCodeListResponse,
  LoginResponse,
  ShipI18nOverrideResponse,
  ShipListResponse,
  SsoAuthorizeResponse,
  StatusSummary,
  TotpSetupResponse,
  UpgradeCostsResponse,
  UpgradePlanResponse,
  UserCreatePayload,
  UserListResponse,
  UserUpdatePayload,
  WebhookLogListResponse,
  WebhookRule,
  WebhookRuleCreatePayload,
  WebhookRuleListResponse,
  WebhookRuleUpdatePayload,
  WebhookTestResponse,
} from "../types/api";

const SESSION_TOKEN_KEY = "ccujailer_session_token";
const ADMIN_TOKEN_KEY = "ccujailer_admin_token";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getSavedSessionToken(): string {
  try {
    return localStorage.getItem(SESSION_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setSavedSessionToken(token: string): void {
  try {
    const cleaned = token.trim();
    if (cleaned) {
      localStorage.setItem(SESSION_TOKEN_KEY, cleaned);
    } else {
      localStorage.removeItem(SESSION_TOKEN_KEY);
    }
  } catch {
    // 忽略本地存储写入受限
  }
}

export function getSavedAdminToken(): string {
  try {
    return localStorage.getItem(ADMIN_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setSavedAdminToken(token: string): void {
  try {
    const cleaned = token.trim();
    if (cleaned) {
      localStorage.setItem(ADMIN_TOKEN_KEY, cleaned);
    } else {
      localStorage.removeItem(ADMIN_TOKEN_KEY);
    }
  } catch {
    // 忽略本地存储写入受限
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function registerUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { skipUnauthorizedHook?: boolean } = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const sessionToken = getSavedSessionToken();
  if (sessionToken) {
    headers.set("Authorization", `Bearer ${sessionToken}`);
  }
  const adminToken = getSavedAdminToken();
  if (adminToken) {
    headers.set("X-Admin-Token", adminToken);
  }

  const resp = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (!resp.ok) {
    let message = `请求失败 (${resp.status})`;
    try {
      const errBody = (await resp.json()) as { detail?: string };
      if (errBody?.detail) {
        message = errBody.detail;
      }
    } catch {
      // 忽略非 JSON 错误体
    }
    if (resp.status === 401 && !options.skipUnauthorizedHook && unauthorizedHandler) {
      unauthorizedHandler();
    }
    throw new ApiError(resp.status, message);
  }

  return (await resp.json()) as T;
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export const apiClient = {
  getAuthStatus(): Promise<AuthStatus> {
    return request<AuthStatus>("/auth/status", {}, { skipUnauthorizedHook: true });
  },

  setupInitialAdmin(payload: {
    username: string;
    password: string;
    display_name?: string;
  }): Promise<LoginResponse> {
    return request<LoginResponse>(
      "/auth/setup",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { skipUnauthorizedHook: true }
    );
  },

  register(payload: {
    username: string;
    password: string;
    display_name?: string;
    email?: string;
    invite_code?: string;
  }): Promise<LoginResponse> {
    return request<LoginResponse>(
      "/auth/register",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { skipUnauthorizedHook: true }
    );
  },

  login(payload: { username: string; password: string }): Promise<LoginResponse> {
    return request<LoginResponse>(
      "/auth/login",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { skipUnauthorizedHook: true }
    );
  },

  loginOtp(payload: { mfa_token: string; otp_code: string }): Promise<LoginResponse> {
    return request<LoginResponse>(
      "/auth/login/otp",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { skipUnauthorizedHook: true }
    );
  },

  logout(): Promise<DeleteResponse> {
    return request<DeleteResponse>(
      "/auth/logout",
      { method: "POST" },
      { skipUnauthorizedHook: true }
    );
  },

  getSsoAuthorizeUrl(redirectUri: string): Promise<SsoAuthorizeResponse> {
    const qs = buildQuery({ redirect_uri: redirectUri });
    return request<SsoAuthorizeResponse>(
      `/auth/sso/authorize${qs}`,
      {},
      { skipUnauthorizedHook: true }
    );
  },

  completeSsoCallback(payload: {
    code: string;
    state: string;
    redirect_uri: string;
  }): Promise<LoginResponse> {
    return request<LoginResponse>(
      "/auth/sso/callback",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
      { skipUnauthorizedHook: true }
    );
  },

  getMe(): Promise<AuthUser> {
    return request<AuthUser>("/auth/me");
  },

  changePassword(payload: { current_password: string; new_password: string }): Promise<LoginResponse> {
    return request<LoginResponse>("/auth/password", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  setupTotp(): Promise<TotpSetupResponse> {
    return request<TotpSetupResponse>("/auth/otp/setup", { method: "POST" });
  },

  enableTotp(otpCode: string): Promise<AuthUser> {
    return request<AuthUser>("/auth/otp/enable", {
      method: "POST",
      body: JSON.stringify({ otp_code: otpCode }),
    });
  },

  disableTotp(payload: { otp_code?: string; current_password?: string }): Promise<AuthUser> {
    return request<AuthUser>("/auth/otp/disable", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  listUsers(): Promise<UserListResponse> {
    return request<UserListResponse>("/users");
  },

  createUser(payload: UserCreatePayload): Promise<AuthUser> {
    return request<AuthUser>("/users", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateUser(userId: number, payload: UserUpdatePayload): Promise<AuthUser> {
    return request<AuthUser>(`/users/${userId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  resetUserOtp(userId: number): Promise<AuthUser> {
    return request<AuthUser>(`/users/${userId}/reset-otp`, { method: "POST" });
  },

  deleteUser(userId: number): Promise<DeleteResponse> {
    return request<DeleteResponse>(`/users/${userId}`, { method: "DELETE" });
  },

  listInvites(): Promise<InviteCodeListResponse> {
    return request<InviteCodeListResponse>("/users/invites");
  },

  createInvite(payload: InviteCodeCreatePayload): Promise<InviteCodeItem> {
    return request<InviteCodeItem>("/users/invites", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  toggleInvite(inviteId: number, isActive: boolean): Promise<InviteCodeItem> {
    return request<InviteCodeItem>(`/users/invites/${inviteId}`, {
      method: "PUT",
      body: JSON.stringify({ is_active: isActive }),
    });
  },

  deleteInvite(inviteId: number): Promise<DeleteResponse> {
    return request<DeleteResponse>(`/users/invites/${inviteId}`, { method: "DELETE" });
  },

  getStatus(): Promise<StatusSummary> {
    return request<StatusSummary>("/status");
  },

  getFullStore(): Promise<FullStoreResponse> {
    return request<FullStoreResponse>("/store");
  },

  getUpgradeCosts(refId: number): Promise<UpgradeCostsResponse> {
    return request<UpgradeCostsResponse>(`/upgrades/${refId}/costs`);
  },

  getUpgradePlanMap(): Promise<UpgradePlanResponse> {
    return request<UpgradePlanResponse>("/plan");
  },

  listCcus(params: {
    q?: string;
    warbond_only?: boolean;
    min_price?: number | null;
    max_price?: number | null;
    min_savings?: number | null;
  }): Promise<CcuListResponse> {
    const qs = buildQuery({
      q: params.q,
      warbond_only: params.warbond_only ? "true" : undefined,
      min_price: params.min_price,
      max_price: params.max_price,
      min_savings: params.min_savings,
    });
    return request<CcuListResponse>(`/ccus${qs}`);
  },

  refreshCcus(options: { syncCatalog?: boolean; enrich?: boolean } = {}): Promise<CcuRefreshResponse> {
    const qs = buildQuery({
      sync_catalog: options.syncCatalog ? "true" : undefined,
      enrich: options.enrich ? "true" : undefined,
    });
    return request<CcuRefreshResponse>(`/ccus/refresh${qs}`, { method: "POST" });
  },

  listShips(params: { q?: string; always_available_only?: boolean }): Promise<ShipListResponse> {
    const qs = buildQuery({
      q: params.q,
      always_available_only: params.always_available_only ? "true" : undefined,
    });
    return request<ShipListResponse>(`/ships${qs}`);
  },

  overrideShipI18n(shipId: number, nameZh: string): Promise<ShipI18nOverrideResponse> {
    return request<ShipI18nOverrideResponse>(`/ships/${shipId}/i18n`, {
      method: "PUT",
      body: JSON.stringify({ name_zh: nameZh }),
    });
  },

  listChangelog(params: {
    limit?: number;
    offset?: number;
    event_type?: string;
    q?: string;
  }): Promise<ChangelogListResponse> {
    const qs = buildQuery(params);
    return request<ChangelogListResponse>(`/changelog${qs}`);
  },

  listWebhooks(): Promise<WebhookRuleListResponse> {
    return request<WebhookRuleListResponse>("/webhooks");
  },

  createWebhook(payload: WebhookRuleCreatePayload): Promise<WebhookRule> {
    return request<WebhookRule>("/webhooks", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateWebhook(ruleId: string, payload: WebhookRuleUpdatePayload): Promise<WebhookRule> {
    return request<WebhookRule>(`/webhooks/${encodeURIComponent(ruleId)}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },

  deleteWebhook(ruleId: string): Promise<DeleteResponse> {
    return request<DeleteResponse>(`/webhooks/${encodeURIComponent(ruleId)}`, {
      method: "DELETE",
    });
  },

  testWebhook(ruleId: string): Promise<WebhookTestResponse> {
    return request<WebhookTestResponse>(`/webhooks/${encodeURIComponent(ruleId)}/test`, {
      method: "POST",
    });
  },

  listWebhookLogs(params: {
    limit?: number;
    offset?: number;
    rule_id?: string;
    success?: boolean | null;
  }): Promise<WebhookLogListResponse> {
    const qs = buildQuery({
      limit: params.limit,
      offset: params.offset,
      rule_id: params.rule_id,
      success:
        params.success === null || params.success === undefined
          ? undefined
          : String(params.success),
    });
    return request<WebhookLogListResponse>(`/webhooks/logs${qs}`);
  },

  syncI18n(): Promise<I18nSyncResponse> {
    return request<I18nSyncResponse>("/i18n/sync", { method: "POST" });
  },

  getSettings(): Promise<DynamicSettings> {
    return request<DynamicSettings>("/settings");
  },

  updateSettings(payload: DynamicSettingsUpdatePayload): Promise<DynamicSettings> {
    return request<DynamicSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
};
