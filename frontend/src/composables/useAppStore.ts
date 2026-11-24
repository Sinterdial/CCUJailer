import { reactive } from "vue";
import {
  apiClient,
  registerUnauthorizedHandler,
  setSavedAdminToken,
  setSavedSessionToken,
} from "../api/client";
import type { AuthStatus, FullStoreResponse, LanguageMode, StatusSummary } from "../types/api";

const LANGUAGE_STORAGE_KEY = "ccujailer_language_mode";
const THEME_STORAGE_KEY = "ccujailer_theme_mode";
const THEME_COLORS = {
  light: "#f5f7fa",
  dark: "#0d1117",
} as const;

let systemThemeListenerAttached = false;

export type ThemeMode = "system" | "light" | "dark";

export interface ToastNotice {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
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
  authLoaded: false,
  authStatus: {
    initialized: true,
    auth_required: true,
    registration_mode: "closed",
    authenticated: false,
    sso_enabled: false,
    sso_provider_name: "ZITADEL",
    upgrade_planner_enabled: false,
    app_version: "0.1.0",
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

export function setThemeMode(mode: ThemeMode): void {
  appStore.themeMode = mode;
  applyThemeToDocument(mode);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  } catch {
    // 忽略本地存储写入失败
  }
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
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, mode);
    } catch {
      // 忽略本地存储写入失败
    }
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
  const auth = await apiClient.getAuthStatus();
  appStore.authStatus = auth;
  appStore.authLoaded = true;

  if (auth.auth_required && !auth.authenticated) {
    return;
  }

  await refreshStoreData();

  if (auth.user?.role === "admin") {
    try {
      const settings = await apiClient.getSettings();
      if (!localStorage.getItem(LANGUAGE_STORAGE_KEY)) {
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
  appStore.storeData = {
    ships: [],
    changes: [],
    game_version: "",
    tracking_since: null,
    checked_at: null,
  };
}
