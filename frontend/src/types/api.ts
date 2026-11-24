export type LanguageMode = "bilingual" | "zh" | "en";

export type UserRole = "admin" | "user";

export type RegistrationMode = "open" | "invite_only" | "closed";

export type WebhookProviderType = "generic" | "feishu" | "wecom" | "discord" | "custom";

export type WebhookMethod = "POST" | "PUT" | "PATCH";

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  totp_enabled: boolean;
  has_password: boolean;
  oidc_bound: boolean;
  last_login_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AuthStatus {
  initialized: boolean;
  auth_required: boolean;
  registration_mode: RegistrationMode;
  authenticated: boolean;
  sso_enabled: boolean;
  sso_provider_name: string;
  upgrade_planner_enabled: boolean;
  app_version: string;
  github_url: string;
  user: AuthUser | null;
}

export interface LoginResponse {
  mfa_required: boolean;
  mfa_token: string | null;
  access_token: string | null;
  expires_at: string | null;
  user: AuthUser | null;
}

export interface TotpSetupResponse {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
}

export interface SsoAuthorizeResponse {
  authorize_url: string;
}

export interface UserListResponse {
  items: AuthUser[];
  total: number;
}

export interface UserCreatePayload {
  username: string;
  password: string;
  display_name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
}

export interface UserUpdatePayload {
  display_name?: string;
  email?: string;
  role?: UserRole;
  is_active?: boolean;
  new_password?: string;
}

export interface StatusSummary {
  total_listed_ccus: number;
  warbond_ccus: number;
  total_ships: number;
  translated_ships: number;
  last_scrape_at: string | null;
}

export interface NearestFromShip {
  name_en: string;
  name_zh: string;
  value: number;
  upgrade_cost: number;
  always_available: boolean;
}

export interface CcuItem {
  sku_id: number;
  ship_id: number;
  rsi_upgrade_id: number | null;
  ship_name_en: string;
  ship_name_zh: string;
  manufacturer: string;
  title: string;
  edition_label: string;
  is_warbond: boolean;
  original_price: number;
  discounted_price: number;
  savings: number;
  currency: string;
  expires_at: string | null;
  store_url: string;
  image_url: string;
  listed_since_at: string;
  last_seen_at: string;
  nearest_from_ships: NearestFromShip[];
}

export interface CcuListResponse {
  items: CcuItem[];
  total: number;
}

export interface CcuRefreshResponse {
  status: string;
  baseline: boolean;
  seen_skus: number;
  listed: number;
  relisted: number;
  price_changed: number;
  delisted: number;
  published_events: number;
  reason: string;
}

export interface ShipItem {
  id: number;
  key: string;
  rsi_upgrade_id: number | null;
  name_en: string;
  name_zh: string;
  auto_translated: boolean;
  manufacturer: string;
  focus: string;
  size: string;
  production_status: string;
  value: number;
  always_available: boolean;
  in_store: boolean;
  store_url: string;
  image_url: string;
  updated_at: string;
}

export interface ShipListResponse {
  items: ShipItem[];
  total: number;
}

export interface ShipI18nOverrideResponse {
  id: number;
  name_en: string;
  name_zh: string;
  auto_translated: boolean;
}

export interface StoreEdition {
  label: string;
  price: number;
  list_price: number | null;
  in_store: boolean;
  available: boolean;
  limited: boolean;
  level: string;
  url: string;
  since: string | null;
  last_seen: string | null;
  insurance: string;
  paints: number;
  includes: string[];
  modules: string[];
}

export interface StoreModule {
  name: string;
  price: number;
  list_price: number | null;
  url: string;
  available: boolean;
  insurance: string;
  label: string;
}

export interface StoreUpgradeOption {
  sku: number;
  label: string;
  price: number;
  is_warbond: boolean;
  expires_at: string | null;
  nearest_from_ships?: NearestFromShip[];
}

export interface StoreWeapons {
  guns: [string, string, number, [number, number | null, string][], number, number, number][];
  pilot: [number, number, number];
  missiles: [number, number | null, string][];
  missileCount: number;
}

export interface CargoGrid {
  scu: number;
  boxes: [number, number, number, number, number, number][];
  viewer: string;
  concept: boolean;
}

export interface StoreShip {
  id: number;
  key: string;
  name_en: string;
  name_zh: string;
  maker: string;
  maker_code: string;
  role: string;
  type: string;
  size: string;
  crew: string;
  cargo: number | null;
  length: number | null;
  status: string;
  loaners: string;
  released: string;
  sold: string;
  gold: { status: string; patch: string; family: string } | null;
  img: string;
  rsi: string;
  always_available: boolean;
  in_store: boolean;
  editions: StoreEdition[];
  modules: StoreModule[];
  ccu: { ref: number; value: number } | null;
  upgrades: StoreUpgradeOption[];
  insurance: { claim: number; expedite: number; expedite_cost: number } | null;
  buy: [number, string][];
  rent: [number, string][];
  weapons: StoreWeapons | null;
  class_name: string;
  history: [string, number, boolean, string][];
  grid: CargoGrid | null;
  module_grids: Record<string, CargoGrid>;
}

export interface FullStoreResponse {
  ships: StoreShip[];
  changes: ChangelogItem[];
  game_version: string;
  tracking_since: string | null;
  checked_at: string | null;
}

export interface UpgradeCostsResponse {
  ref: number;
  at: number;
  costs: Record<string, number>;
}

export interface UpgradePlanResponse {
  at: string | null;
  estimated: boolean;
  ships: [number, string, string, number, boolean, boolean][];
  skus: Record<string, [number, string, number, boolean]>;
  paths: Record<string, [number, number][]>;
}

export interface ChangelogItem {
  id: number;
  occurred_at: string;
  event_type: string;
  sku_id: number;
  ship_name_en: string;
  ship_name_zh: string;
  edition_label: string;
  is_warbond: boolean;
  old_price: number | null;
  new_price: number | null;
  original_price: number | null;
  details: Record<string, unknown>;
}

export interface ChangelogListResponse {
  items: ChangelogItem[];
  total: number;
}

export interface WebhookFilters {
  only_warbond: boolean;
  ship_keywords: string[];
  min_original_price: number | null;
  max_original_price: number | null;
  min_wb_price: number | null;
  max_wb_price: number | null;
  min_savings: number | null;
}

export interface WebhookRule {
  id: string;
  name: string;
  enabled: boolean;
  url: string;
  method: WebhookMethod;
  provider_type: WebhookProviderType;
  custom_template: string | null;
  headers: Record<string, string>;
  events: string[];
  filters: WebhookFilters;
  created_at: string;
  updated_at: string;
}

export interface WebhookRuleListResponse {
  items: WebhookRule[];
  total: number;
}

export interface WebhookRuleCreatePayload {
  id?: string | null;
  name: string;
  enabled: boolean;
  url: string;
  method: WebhookMethod;
  provider_type: WebhookProviderType;
  custom_template: string | null;
  headers: Record<string, string>;
  events: string[];
  filters: WebhookFilters;
}

export interface WebhookRuleUpdatePayload {
  name?: string;
  enabled?: boolean;
  url?: string;
  method?: WebhookMethod;
  provider_type?: WebhookProviderType;
  custom_template?: string | null;
  headers?: Record<string, string>;
  events?: string[];
  filters?: WebhookFilters;
}

export interface DeleteResponse {
  deleted: boolean;
  id: string;
}

export interface WebhookTestResponse {
  rule_id: string;
  rule_name: string;
  success: boolean;
  status_code: number | null;
  attempt_count: number;
  duration_ms: number;
  error_message: string;
}

export interface WebhookLogItem {
  id: number;
  rule_id: string;
  rule_name: string;
  event_type: string;
  ship_name: string;
  status_code: number | null;
  success: boolean;
  attempt_count: number;
  duration_ms: number;
  error_message: string;
  created_at: string;
}

export interface WebhookLogListResponse {
  items: WebhookLogItem[];
  total: number;
}

export interface I18nSyncResponse {
  remote_entries: number;
  total_ships: number;
  updated_ships: number;
}

export interface InviteCodeItem {
  id: number;
  code: string;
  role: UserRole;
  note: string;
  max_uses: number;
  used_count: number;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface InviteCodeListResponse {
  items: InviteCodeItem[];
  total: number;
}

export interface InviteCodeCreatePayload {
  role: UserRole;
  max_uses: number;
  expires_in_hours: number | null;
  note: string;
}

export interface DynamicSettings {
  scrape_interval_seconds: number;
  catalog_sync_interval_seconds: number;
  i18n_source_url: string;
  default_language: LanguageMode;
  log_retention_days: number;
  translate_enabled: boolean;
  translate_api_base: string;
  translate_model: string;
  translate_api_key_configured: boolean;
  auth_require_login: boolean;
  registration_mode: RegistrationMode;
  sso_enabled: boolean;
  sso_provider_name: string;
  sso_issuer_url: string;
  sso_client_id: string;
  sso_scopes: string;
  sso_auto_register: boolean;
  sso_default_role: UserRole;
  upgrade_planner_enabled: boolean;
}

export interface DynamicSettingsUpdatePayload {
  scrape_interval_seconds?: number;
  catalog_sync_interval_seconds?: number;
  i18n_source_url?: string;
  default_language?: LanguageMode;
  log_retention_days?: number;
  translate_enabled?: boolean;
  translate_api_base?: string;
  translate_model?: string;
  registration_mode?: RegistrationMode;
  sso_enabled?: boolean;
  sso_provider_name?: string;
  sso_issuer_url?: string;
  sso_client_id?: string;
  sso_scopes?: string;
  sso_auto_register?: boolean;
  sso_default_role?: UserRole;
  upgrade_planner_enabled?: boolean;
}
