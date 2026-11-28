import { reactive } from "vue";
import {
  apiClient,
  registerUnauthorizedHandler,
  setSavedAdminToken,
  setSavedSessionToken,
} from "../api/client";
import type {
  AuthStatus,
  FullStoreResponse,
  LanguageMode,
  StatusSummary,
  ThemeMode,
  UserPreferences,
} from "../types/api";

export type { ThemeMode };

const LANGUAGE_STORAGE_KEY = "ccujailer_language_mode";
const THEME_STORAGE_KEY = "ccujailer_theme_mode";
const RATE_STORAGE_KEY = "ccujailer_auec_rate";
const DETAILS_ORDER_KEY = "ccujailer_details_card_order";
const DETAILS_COLLAPSED_KEY = "ccujailer_details_collapsed_cards";
const WEBHOOK_ORDER_KEY = "ccujailer_webhook_custom_order";
const WEBHOOK_COLLAPSED_KEY = "ccujailer_webhook_collapsed_ids";
const OWNED_CCUS_KEY = "ccujailer_owned_ccus";
const DEFAULT_FILTERS_KEY = "ccujailer_user_default_filters";
const DEFAULT_SORT_KEY = "ccujailer_user_default_sort";
const STORE_PAGE_SIZE_KEY = "ccujailer_store_page_size";
export const DEFAULT_STORE_PAGE_SIZE = 20;

export const DEFAULT_DETAIL_CARD_ORDER = [
  "ingame",
  "insurance",
  "weapons",
  "loadouts",
  "donors",
  "history",
  "cargo",
] as const;

const THEME_COLORS = {
  light: "#f5f7fa",
  dark: "#0d1117",
} as const;

let systemThemeListenerAttached = false;
let pendingPrefUpdates: Record<string, unknown> = {};
let prefSyncTimer: number | null = null;

export interface ToastNotice {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}

function readJsonStorage<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return fallback;
    }
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJsonStorage(key: string, val: unknown): void {
  try {
    if (val === null || val === undefined) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify(val));
    }
  } catch {
    // 忽略本地存储写入失败
  }
}

function readInitialLanguage(): LanguageMode {
  try {
    const val = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (val === "bilingual" || val === "zh" || val === "en") {
      return val;
    }
  } catch {
    // 忽略浏览器存储访问受限
  }
  return "bilingual";
}

function readInitialTheme(): ThemeMode {
  try {
    const val = localStorage.getItem(THEME_STORAGE_KEY);
    if (val === "system" || val === "light" || val === "dark") {
      return val;
    }
  } catch {
    // 忽略浏览器存储访问受限
  }
  return "system";
}

function readLocalPreferencesFallback(): UserPreferences {
  const rateRaw = (() => {
    try {
      return Number(localStorage.getItem(RATE_STORAGE_KEY)) || 500000;
    } catch {
      return 500000;
    }
  })();

  const dOrder = readJsonStorage<unknown>(DETAILS_ORDER_KEY, [...DEFAULT_DETAIL_CARD_ORDER]);
  const dCollapsed = readJsonStorage<unknown>(DETAILS_COLLAPSED_KEY, []);
  const wOrder = readJsonStorage<unknown>(WEBHOOK_ORDER_KEY, []);
  const wCollapsed = readJsonStorage<unknown>(WEBHOOK_COLLAPSED_KEY, []);
  const owned = readJsonStorage<unknown>(OWNED_CCUS_KEY, []);
  const defFilters = readJsonStorage<unknown>(DEFAULT_FILTERS_KEY, null);
  const defSort = (() => {
    try {
      return localStorage.getItem(DEFAULT_SORT_KEY) || null;
    } catch {
      return null;
    }
  })();
  const pageSizeRaw = (() => {
    try {
      const raw = localStorage.getItem(STORE_PAGE_SIZE_KEY);
      if (raw === null) {
        return DEFAULT_STORE_PAGE_SIZE;
      }
      const parsed = Number(raw);
      return Number.isFinite(parsed) && parsed >= 0 ? Math.min(500, Math.round(parsed)) : DEFAULT_STORE_PAGE_SIZE;
    } catch {
      return DEFAULT_STORE_PAGE_SIZE;
    }
  })();

  return {
    language_mode: readInitialLanguage(),
    theme_mode: readInitialTheme(),
    auec_rate: rateRaw,
    details_card_order: Array.isArray(dOrder)
      ? dOrder.filter((v): v is string => typeof v === "string")
      : [...DEFAULT_DETAIL_CARD_ORDER],
    details_collapsed_cards: Array.isArray(dCollapsed)
      ? dCollapsed.filter((v): v is string => typeof v === "string")
      : [],
    webhook_card_order: Array.isArray(wOrder)
      ? wOrder.filter((v): v is string => typeof v === "string")
      : [],
    webhook_collapsed_ids: Array.isArray(wCollapsed)
      ? wCollapsed.filter((v): v is string => typeof v === "string")
      : [],
    owned_ccus: Array.isArray(owned)
      ? owned.filter((p): p is [number, number] => Array.isArray(p) && p.length === 2)
      : [],
    default_store_filters: Array.isArray(defFilters)
      ? defFilters.filter((v): v is string => typeof v === "string")
      : null,
    default_store_sort: defSort,
    store_page_size: pageSizeRaw,
  };
}

function mirrorPreferencesToLocalStorage(prefs: UserPreferences): void {
  try {
    if (prefs.language_mode) {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, prefs.language_mode);
    }
    if (prefs.theme_mode) {
      localStorage.setItem(THEME_STORAGE_KEY, prefs.theme_mode);
    }
    if (typeof prefs.auec_rate === "number" && prefs.auec_rate > 0) {
      localStorage.setItem(RATE_STORAGE_KEY, String(prefs.auec_rate));
    }
  } catch {
    // 忽略本地存储写入失败
  }
  if (Array.isArray(prefs.details_card_order)) {
    writeJsonStorage(DETAILS_ORDER_KEY, prefs.details_card_order);
  }
  if (Array.isArray(prefs.details_collapsed_cards)) {
    writeJsonStorage(DETAILS_COLLAPSED_KEY, prefs.details_collapsed_cards);
  }
  if (Array.isArray(prefs.webhook_card_order)) {
    writeJsonStorage(WEBHOOK_ORDER_KEY, prefs.webhook_card_order);
  }
  if (Array.isArray(prefs.webhook_collapsed_ids)) {
    writeJsonStorage(WEBHOOK_COLLAPSED_KEY, prefs.webhook_collapsed_ids);
  }
  if (Array.isArray(prefs.owned_ccus)) {
    writeJsonStorage(OWNED_CCUS_KEY, prefs.owned_ccus);
  }
  if ("default_store_filters" in prefs) {
    writeJsonStorage(DEFAULT_FILTERS_KEY, prefs.default_store_filters ?? null);
  }
  if ("default_store_sort" in prefs) {
    try {
      if (prefs.default_store_sort) {
        localStorage.setItem(DEFAULT_SORT_KEY, prefs.default_store_sort);
      } else {
        localStorage.removeItem(DEFAULT_SORT_KEY);
      }
    } catch {
      // 忽略本地存储异常
    }
  }
  if (typeof prefs.store_page_size === "number") {
    try {
      localStorage.setItem(STORE_PAGE_SIZE_KEY, String(prefs.store_page_size));
    } catch {
      // 忽略本地存储异常
    }
  }
}

function resolveIsDarkTheme(mode: ThemeMode): boolean {
  if (mode === "dark") {
    return true;
  }
  if (mode === "light") {
    return false;
  }
  return (
    typeof window !== "undefined" &&
    Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches)
  );
}

function syncThemeColorMeta(mode: ThemeMode): void {
  const meta = document.getElementById("theme-color-meta");
  if (meta) {
    meta.setAttribute("content", resolveIsDarkTheme(mode) ? THEME_COLORS.dark : THEME_COLORS.light);
  }
}

function applyThemeToDocument(mode: ThemeMode): void {
  if (mode === "light" || mode === "dark") {
    document.documentElement.dataset.theme = mode;
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  syncThemeColorMeta(mode);
}

function ensureSystemThemeSync(): void {
  if (systemThemeListenerAttached || typeof window === "undefined" || !window.matchMedia) {
    return;
  }
  systemThemeListenerAttached = true;
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (appStore.themeMode === "system") {
      syncThemeColorMeta("system");
    }
  };
  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", onChange);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(onChange);
  }
}

export const appStore = reactive({
  languageMode: readInitialLanguage(),
  themeMode: readInitialTheme(),
  userPreferences: readLocalPreferencesFallback(),
  preferencesSyncVersion: 0,
  authLoaded: false,
  authStatus: {
    initialized: true,
    auth_required: true,
    registration_mode: "closed",
    authenticated: false,
    sso_enabled: false,
    sso_provider_name: "ZITADEL",
    passkey_enabled: true,
    default_store_filters: [],
    default_store_sort: "price-desc",
    upgrade_planner_enabled: false,
    app_version: "0.2.0",
    github_url: "https://github.com/Sinterdial/CCUJailer",
    user: null,
  } as AuthStatus,
  statusSummary: {
    total_listed_ccus: 0,
    warbond_ccus: 0,
    total_ships: 0,
    translated_ships: 0,
    last_scrape_at: null,
  } as StatusSummary,
  storeData: {
    ships: [],
    changes: [],
    game_version: "",
    tracking_since: null,
    checked_at: null,
  } as FullStoreResponse,
  storeLoaded: false,
  toasts: [] as ToastNotice[],
  nextToastId: 1,
});

applyThemeToDocument(appStore.themeMode);
ensureSystemThemeSync();

registerUnauthorizedHandler(() => {
  setSavedSessionToken("");
  appStore.authStatus.authenticated = false;
  appStore.authStatus.user = null;
  appStore.storeLoaded = false;
});

function hydratePreferencesFromServer(serverPrefs: UserPreferences | undefined): void {
  if (!serverPrefs) {
    return;
  }
  const merged: UserPreferences = {
    language_mode: serverPrefs.language_mode ?? appStore.userPreferences.language_mode ?? "bilingual",
    theme_mode: serverPrefs.theme_mode ?? appStore.userPreferences.theme_mode ?? "system",
    auec_rate: serverPrefs.auec_rate ?? appStore.userPreferences.auec_rate ?? 500000,
    details_card_order:
      serverPrefs.details_card_order ?? [...DEFAULT_DETAIL_CARD_ORDER],
    details_collapsed_cards: serverPrefs.details_collapsed_cards ?? [],
    webhook_card_order: serverPrefs.webhook_card_order ?? [],
    webhook_collapsed_ids: serverPrefs.webhook_collapsed_ids ?? [],
    owned_ccus: serverPrefs.owned_ccus ?? [],
    default_store_filters:
      serverPrefs.default_store_filters !== undefined ? serverPrefs.default_store_filters : null,
    default_store_sort:
      serverPrefs.default_store_sort !== undefined ? serverPrefs.default_store_sort : null,
    store_page_size:
      typeof serverPrefs.store_page_size === "number"
        ? serverPrefs.store_page_size
        : appStore.userPreferences.store_page_size ?? DEFAULT_STORE_PAGE_SIZE,
  };
  appStore.userPreferences = merged;
  if (serverPrefs.language_mode) {
    appStore.languageMode = serverPrefs.language_mode;
  }
  if (serverPrefs.theme_mode) {
    appStore.themeMode = serverPrefs.theme_mode;
    applyThemeToDocument(serverPrefs.theme_mode);
  }
  mirrorPreferencesToLocalStorage(merged);
  appStore.preferencesSyncVersion += 1;
}

async function flushPendingPreferenceSync(): Promise<void> {
  if (prefSyncTimer !== null) {
    window.clearTimeout(prefSyncTimer);
    prefSyncTimer = null;
  }
  if (!Object.keys(pendingPrefUpdates).length) {
    return;
  }
  if (!appStore.authStatus.authenticated || !appStore.authStatus.user || appStore.authStatus.user.id <= 0) {
    pendingPrefUpdates = {};
    return;
  }
  const batch = pendingPrefUpdates as Partial<UserPreferences>;
  pendingPrefUpdates = {};
  try {
    const saved = await apiClient.updatePreferences(batch);
    if (appStore.authStatus.user) {
      appStore.authStatus.user.preferences = saved;
    }
  } catch {
    // 保留本地更新，避免网络波动打断当前交互
  }
}

export async function updateUserPreferences(
  partial: Partial<UserPreferences>,
  options: { syncRemote?: boolean; immediate?: boolean } = {}
): Promise<void> {
  const { syncRemote = true, immediate = false } = options;
  const next: UserPreferences = { ...appStore.userPreferences };

  for (const [k, v] of Object.entries(partial)) {
    if (v === undefined) {
      continue;
    }
    (next as Record<string, unknown>)[k] = v;
    if (syncRemote) {
      pendingPrefUpdates[k] = v;
    }
  }

  appStore.userPreferences = next;
  mirrorPreferencesToLocalStorage(next);
  appStore.preferencesSyncVersion += 1;

  if (!syncRemote) {
    return;
  }
  if (!appStore.authStatus.authenticated || !appStore.authStatus.user || appStore.authStatus.user.id <= 0) {
    pendingPrefUpdates = {};
    return;
  }

  if (immediate) {
    await flushPendingPreferenceSync();
    return;
  }
  if (prefSyncTimer !== null) {
    window.clearTimeout(prefSyncTimer);
  }
  prefSyncTimer = window.setTimeout(() => {
    void flushPendingPreferenceSync();
  }, 250);
}

export function getEffectiveDefaultStoreFilters(): string[] {
  const userFilters = appStore.userPreferences.default_store_filters;
  if (Array.isArray(userFilters)) {
    return [...userFilters];
  }
  return [...(appStore.authStatus.default_store_filters || [])];
}

export function getEffectiveDefaultStoreSort(): string {
  return (
    appStore.userPreferences.default_store_sort ||
    appStore.authStatus.default_store_sort ||
    "price-desc"
  );
}

export function setThemeMode(mode: ThemeMode, syncRemote = true): void {
  appStore.themeMode = mode;
  applyThemeToDocument(mode);
  void updateUserPreferences({ theme_mode: mode }, { syncRemote });
}

export function dismissToast(id: number): void {
  const idx = appStore.toasts.findIndex((t) => t.id === id);
  if (idx !== -1) {
    appStore.toasts.splice(idx, 1);
  }
}

export function notify(text: string, kind: "success" | "error" | "info" = "info"): void {
  while (appStore.toasts.length >= 2) {
    appStore.toasts.shift();
  }
  const id = appStore.nextToastId++;
  appStore.toasts.push({ id, kind, text });
  window.setTimeout(() => {
    dismissToast(id);
  }, 3200);
}

export function setLanguageMode(mode: LanguageMode, persistLocal = true): void {
  appStore.languageMode = mode;
  if (persistLocal) {
    void updateUserPreferences({ language_mode: mode }, { syncRemote: true });
  }
}

export function isAdminUser(): boolean {
  return appStore.authStatus.authenticated && appStore.authStatus.user?.role === "admin";
}

export function ensureWriteAuthorized(): boolean {
  if (!appStore.authStatus.authenticated) {
    notify("请先登录后再执行操作", "error");
    return false;
  }
  if (appStore.authStatus.user?.role !== "admin") {
    notify("该操作仅限管理员执行", "error");
    return false;
  }
  return true;
}

export function formatShipDisplay(
  nameEn: string,
  nameZh: string,
  mode = appStore.languageMode
): string {
  const cleanEn = nameEn.trim();
  const cleanZh = nameZh.trim();
  if (mode === "en") {
    return cleanEn;
  }
  if (mode === "zh") {
    return cleanZh || cleanEn;
  }
  if (cleanZh && cleanZh !== cleanEn) {
    return `${cleanZh} / ${cleanEn}`;
  }
  return cleanEn;
}

export function moneyFromCents(cents: number | null | undefined): string {
  if (cents == null) {
    return "";
  }
  return (
    "$" +
    (cents / 100).toLocaleString("en-US", {
      maximumFractionDigits: cents % 100 ? 2 : 0,
    })
  );
}

export function formatAuec(amount: number): string {
  if (amount >= 1e6) {
    return `${+(amount / 1e6).toFixed(amount >= 1e7 ? 1 : 2)}M aUEC`;
  }
  if (amount >= 1e3) {
    return `${Math.round(amount / 1e3)}k aUEC`;
  }
  return `${amount} aUEC`;
}

export function formatHoursLabel(hoursVal: number): string {
  if (hoursVal < 1) {
    return `${Math.max(5, Math.round((hoursVal * 60) / 5) * 5)} 分钟`;
  }
  return hoursVal < 10 ? `${+hoursVal.toFixed(1)} 小时` : `${Math.round(hoursVal)} 小时`;
}

export function formatAgo(iso: string | null | undefined): string {
  if (!iso) {
    return "刚刚";
  }
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) {
    return iso;
  }
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 90) {
    return "刚刚";
  }
  if (sec < 3600) {
    return `${Math.round(sec / 60)} 分钟前`;
  }
  if (sec < 86400) {
    return `${Math.round(sec / 3600)} 小时前`;
  }
  const days = Math.round(sec / 86400);
  return days < 60 ? `${days} 天前` : `${Math.round(days / 30)} 个月前`;
}

export function formatUtcTime(iso: string | null | undefined): string {
  if (!iso) {
    return "未公布";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export async function refreshStoreData(): Promise<void> {
  if (appStore.authStatus.auth_required && !appStore.authStatus.authenticated) {
    return;
  }
  const [status, fullStore] = await Promise.all([
    apiClient.getStatus(),
    apiClient.getFullStore(),
  ]);
  appStore.statusSummary = status;
  appStore.storeData = fullStore;
  appStore.storeLoaded = true;
}

export async function refreshBootstrapState(): Promise<void> {
  await flushPendingPreferenceSync();
  const auth = await apiClient.getAuthStatus();
  appStore.authStatus = auth;
  appStore.authLoaded = true;

  if (auth.authenticated && auth.user && auth.user.id > 0) {
    hydratePreferencesFromServer(auth.user.preferences);
  } else {
    appStore.preferencesSyncVersion += 1;
  }

  if (auth.auth_required && !auth.authenticated) {
    return;
  }

  await refreshStoreData();

  if (auth.user?.role === "admin") {
    try {
      const settings = await apiClient.getSettings();
      if (!auth.user?.preferences?.language_mode && !localStorage.getItem(LANGUAGE_STORAGE_KEY)) {
        appStore.languageMode = settings.default_language;
      }
    } catch {
      // 忽略无权限读取系统配置异常
    }
  }
}

export async function applySessionLogin(token: string | null): Promise<void> {
  if (token) {
    setSavedSessionToken(token);
  }
  await refreshBootstrapState();
}

export async function logoutSession(): Promise<void> {
  if (prefSyncTimer !== null) {
    window.clearTimeout(prefSyncTimer);
    prefSyncTimer = null;
  }
  pendingPrefUpdates = {};
  try {
    await apiClient.logout();
  } catch {
    // 忽略退出异常
  }
  setSavedSessionToken("");
  setSavedAdminToken("");
  appStore.toasts.splice(0);
  appStore.authStatus.authenticated = false;
  appStore.authStatus.user = null;
  appStore.storeLoaded = false;
  appStore.userPreferences = {
    language_mode: appStore.languageMode,
    theme_mode: appStore.themeMode,
    auec_rate: 500000,
    details_card_order: [...DEFAULT_DETAIL_CARD_ORDER],
    details_collapsed_cards: [],
    webhook_card_order: [],
    webhook_collapsed_ids: [],
    owned_ccus: [],
    default_store_filters: null,
    default_store_sort: null,
    store_page_size: DEFAULT_STORE_PAGE_SIZE,
  };
  mirrorPreferencesToLocalStorage(appStore.userPreferences);
  appStore.preferencesSyncVersion += 1;
  appStore.storeData = {
    ships: [],
    changes: [],
    game_version: "",
    tracking_since: null,
    checked_at: null,
  };
}
