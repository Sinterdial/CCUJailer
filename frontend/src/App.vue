<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import {
  ChevronDown,
  Fingerprint,
  KeyRound,
  LogOut,
  Monitor,
  Moon,
  RefreshCw,
  Settings,
  Shield,
  Sun,
  UserCheck,
  UserCog,
  UserPlus,
  X,
} from "lucide-vue-next";
import { apiClient } from "./api/client";
import AccountSecurityPanel from "./components/AccountSecurityPanel.vue";
import AuthView from "./components/AuthView.vue";
import ChangelogView from "./components/ChangelogView.vue";
import SettingsView, { type SettingsSubTab } from "./components/SettingsView.vue";
import StoreView from "./components/StoreView.vue";
import UpgradePlannerView from "./components/UpgradePlannerView.vue";
import WebhooksView from "./components/WebhooksView.vue";
import {
  applySessionLogin,
  appStore,
  dismissToast,
  ensureWriteAuthorized,
  formatAgo,
  isAdminUser,
  logoutSession,
  notify,
  refreshBootstrapState,
  refreshStoreData,
  setLanguageMode,
  setThemeMode,
  type ThemeMode,
} from "./composables/useAppStore";
import type { LanguageMode } from "./types/api";
import { getPasskeyAssertion, isPasskeyOriginUsable } from "./utils/webauthn";

type TabId = "store" | "all" | "changes" | "plan" | "webhooks" | "settings" | "account";

const activeTab = ref<TabId>("store");
const activeStoreViewMode = ref<"store" | "all">("store");
const tabScrollPositions = reactive<Record<TabId, number>>({
  store: 0,
  all: 0,
  changes: 0,
  plan: 0,
  webhooks: 0,
  settings: 0,
  account: 0,
});
const refreshing = ref(false);
const planFromKey = ref("");
const planToRef = ref<number | null>(null);
const accountMenuOpen = ref(false);
const accountMenuRef = ref<HTMLElement | null>(null);
const settingsSubTab = ref<SettingsSubTab>("general");
const settingsSubTabKey = ref(0);
const createUserSignal = ref(0);

const switchModalOpen = ref(false);
const switchSubmitting = ref(false);
const switchMfaToken = ref("");
const switchError = ref("");
const switchForm = reactive({
  username: "",
  password: "",
  otpCode: "",
});

let autoRefreshTimer: number | null = null;

const needsLogin = computed(
  () =>
    appStore.authLoaded &&
    ((appStore.authStatus.auth_required && !appStore.authStatus.authenticated) ||
      !appStore.authStatus.initialized ||
      window.location.pathname === "/auth/callback")
);

const inStoreCount = computed(
  () => appStore.storeData.ships.filter((s) => s.in_store).length
);

const ledeStats = computed(() => {
  const inStore = appStore.storeData.ships.filter((s) => s.in_store);
  const warbondCount = inStore.filter((s) =>
    s.editions.some((e) => e.in_store && e.label === "Warbond")
  ).length;
  const tenYearCount = inStore.filter((s) =>
    s.editions.some((e) => e.in_store && e.label === "10 Year")
  ).length;
  const discountedCount = inStore.filter((s) =>
    s.editions.some((e) => e.in_store && e.list_price !== null && e.price < e.list_price)
  ).length;
  const limitedCount = inStore.filter((s) =>
    s.editions.some((e) => e.in_store && e.limited)
  ).length;

  return {
    warbondCount,
    tenYearCount,
    discountedCount,
    limitedCount,
    ccuWarbondCount: appStore.statusSummary.warbond_ccus,
  };
});

const plannerEnabled = computed(() => Boolean(appStore.authStatus.upgrade_planner_enabled));

watch(plannerEnabled, (enabled) => {
  if (!enabled && activeTab.value === "plan") {
    switchTab("store");
  }
});

const visibleTabs = computed<{ id: TabId; label: string }[]>(() => {
  const tabs: { id: TabId; label: string }[] = [
    { id: "store", label: `在售舰船 · ${inStoreCount.value}` },
    { id: "all", label: `全部舰船 · ${appStore.storeData.ships.length}` },
    { id: "changes", label: `变动记录 · ${appStore.storeData.changes.length}` },
  ];
  if (plannerEnabled.value) {
    tabs.push({ id: "plan", label: "升级路线规划" });
  }
  if (isAdminUser()) {
    tabs.push({ id: "webhooks", label: "推送设置" });
    tabs.push({ id: "settings", label: "系统设置" });
  }
  return tabs;
});

function switchTab(nextTab: TabId): void {
  const prevTab = activeTab.value;
  tabScrollPositions[prevTab] = window.scrollY;
  if (nextTab === "store" || nextTab === "all") {
    activeStoreViewMode.value = nextTab;
  }
  activeTab.value = nextTab;
  const restoreY = tabScrollPositions[nextTab] || 0;
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: restoreY, behavior: "auto" });
    });
  });
}

async function handleManualRefresh(): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  refreshing.value = true;
  try {
    const res = await apiClient.refreshCcus({ syncCatalog: false, enrich: false });
    if (res.status === "ok") {
      notify(
        `同步完成：当前在售 ${res.seen_skus} 条 CCU（新上架 ${res.listed}，降价 ${res.price_changed}）`,
        "success"
      );
    } else {
      notify(`同步未执行 (${res.status})：${res.reason}`, "error");
    }
    await refreshStoreData();
  } catch (err) {
    notify(err instanceof Error ? err.message : "触发同步失败", "error");
  } finally {
    refreshing.value = false;
  }
}

function handleOpenPlan(payload: { fromKey: string; toRef: number }): void {
  if (!plannerEnabled.value) {
    return;
  }
  planFromKey.value = payload.fromKey;
  planToRef.value = payload.toRef;
  switchTab("plan");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function handleLangChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value as LanguageMode;
  setLanguageMode(value, true);
}

function handleThemeChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value as ThemeMode;
  setThemeMode(value);
}

function openAccountSettings(): void {
  accountMenuOpen.value = false;
  switchTab("account");
}

function openSettingsSection(subTab: SettingsSubTab, openCreateUser = false): void {
  accountMenuOpen.value = false;
  if (!isAdminUser()) {
    switchTab("account");
    return;
  }
  settingsSubTab.value = subTab;
  settingsSubTabKey.value += 1;
  switchTab("settings");
  if (openCreateUser) {
    createUserSignal.value += 1;
  }
}

function openSwitchAccountModal(): void {
  accountMenuOpen.value = false;
  switchMfaToken.value = "";
  switchError.value = "";
  switchForm.username = "";
  switchForm.password = "";
  switchForm.otpCode = "";
  switchModalOpen.value = true;
}

function closeSwitchModal(): void {
  switchModalOpen.value = false;
  switchMfaToken.value = "";
  switchError.value = "";
}

async function submitSwitchAccount(): Promise<void> {
  switchError.value = "";
  switchSubmitting.value = true;
  try {
    const res = await apiClient.login({
      username: switchForm.username.trim(),
      password: switchForm.password,
    });
    if (res.mfa_required && res.mfa_token) {
      switchMfaToken.value = res.mfa_token;
      switchForm.otpCode = "";
      notify("请输入新账号的 6 位 OTP 动态口令", "info");
      return;
    }
    await applySessionLogin(res.access_token);
    closeSwitchModal();
    if (!isAdminUser() && (activeTab.value === "settings" || activeTab.value === "webhooks")) {
      switchTab("store");
    }
    notify(
      `已切换至账号「${appStore.authStatus.user?.username || switchForm.username.trim()}」`,
      "success"
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "切换账号失败";
    switchError.value = msg;
    notify(msg, "error");
  } finally {
    switchSubmitting.value = false;
  }
}

async function submitSwitchOtp(): Promise<void> {
  switchError.value = "";
  switchSubmitting.value = true;
  try {
    const res = await apiClient.loginOtp({
      mfa_token: switchMfaToken.value,
      otp_code: switchForm.otpCode.trim(),
    });
    await applySessionLogin(res.access_token);
    closeSwitchModal();
    if (!isAdminUser() && (activeTab.value === "settings" || activeTab.value === "webhooks")) {
      switchTab("store");
    }
    notify(`已切换至账号「${appStore.authStatus.user?.username || ""}」`, "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "OTP 验证失败";
    switchError.value = msg;
    notify(msg, "error");
  } finally {
    switchSubmitting.value = false;
  }
}

async function startSwitchSso(): Promise<void> {
  switchError.value = "";
  try {
    const callbackUri = `${window.location.origin}/auth/callback`;
    const { authorize_url } = await apiClient.getSsoAuthorizeUrl(callbackUri);
    window.location.href = authorize_url;
  } catch (err) {
    const msg = err instanceof Error ? err.message : "启动 SSO 登录失败";
    switchError.value = msg;
    notify(msg, "error");
  }
}

const passkeySwitchUsable = computed(
  () => appStore.authStatus.passkey_enabled && isPasskeyOriginUsable().ok
);

async function startSwitchPasskey(): Promise<void> {
  switchError.value = "";
  const originCheck = isPasskeyOriginUsable();
  if (!originCheck.ok) {
    switchError.value = originCheck.reason;
    notify(originCheck.reason, "error");
    return;
  }
  switchSubmitting.value = true;
  try {
    const challenge = await apiClient.beginPasskeyLogin(switchForm.username.trim());
    const credential = await getPasskeyAssertion(challenge.options);
    const res = await apiClient.verifyPasskeyLogin({
      challenge_id: challenge.challenge_id,
      credential,
    });
    await applySessionLogin(res.access_token);
    closeSwitchModal();
    if (!isAdminUser() && (activeTab.value === "settings" || activeTab.value === "webhooks")) {
      activeTab.value = "store";
    }
    notify(`已通过 Passkey 切换至账号「${appStore.authStatus.user?.username || ""}」`, "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Passkey 切换账号失败";
    switchError.value = msg;
    notify(msg, "error");
  } finally {
    switchSubmitting.value = false;
  }
}

async function handleLogout(): Promise<void> {
  accountMenuOpen.value = false;
  await logoutSession();
  activeTab.value = "store";
  notify("已退出登录", "info");
}

function handleDocumentClick(event: MouseEvent): void {
  if (
    accountMenuOpen.value &&
    accountMenuRef.value &&
    !accountMenuRef.value.contains(event.target as Node)
  ) {
    accountMenuOpen.value = false;
  }
}

function handleGlobalKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    accountMenuOpen.value = false;
    if (switchModalOpen.value) {
      closeSwitchModal();
    }
  }
}

function handleVisibilityRefresh(): void {
  if (!document.hidden && !needsLogin.value && appStore.authLoaded) {
    void refreshStoreData().catch(() => {});
  }
}

onMounted(() => {
  void refreshBootstrapState().catch((err) => {
    notify(err instanceof Error ? err.message : "初始化数据加载失败", "error");
  });
  autoRefreshTimer = window.setInterval(() => {
    if (!document.hidden && !needsLogin.value && appStore.authLoaded) {
      void refreshStoreData().catch(() => {});
    }
  }, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", handleVisibilityRefresh);
  document.addEventListener("click", handleDocumentClick);
  window.addEventListener("keydown", handleGlobalKeydown);
});

onUnmounted(() => {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer);
  }
  document.removeEventListener("visibilitychange", handleVisibilityRefresh);
  document.removeEventListener("click", handleDocumentClick);
  window.removeEventListener("keydown", handleGlobalKeydown);
});
</script>

<template>
  <div class="page">
    <a href="#main-content" class="skip-link">跳至主要内容</a>

    <!-- 顶部公共导航栏 -->
    <header class="top">
      <div class="brand-wrap">
        <a class="mark" href="/" aria-label="CCUJailer home" @click.prevent="activeTab = 'store'">
          <img src="/favicon.svg" alt="" aria-hidden="true" class="brand-logo" />
          <span translate="no">CCUJailer</span>
        </a>
        <span
          class="version-badge"
          translate="no"
          :title="`当前服务版本 v${appStore.authStatus.app_version}`"
        >
          v{{ appStore.authStatus.app_version }}
        </span>
      </div>

      <p v-if="!needsLogin" class="checked">
        <span class="dot" aria-hidden="true" />
        <span>
          <span translate="no">RSI</span> 商店 · 最近检查
          {{
            appStore.storeData.checked_at
              ? formatAgo(appStore.storeData.checked_at)
              : appStore.statusSummary.last_scrape_at
                ? formatAgo(appStore.statusSummary.last_scrape_at)
                : "等待首次同步"
          }}
        </span>
      </p>

      <div class="menu">
        <select
          v-if="!needsLogin"
          :value="appStore.languageMode"
          aria-label="船名显示语言"
          class="navselect"
          @change="handleLangChange"
        >
          <option value="bilingual">中英双语</option>
          <option value="zh">仅中文</option>
          <option value="en">仅英文</option>
        </select>

        <div class="theme-switch">
          <Monitor v-if="appStore.themeMode === 'system'" :size="14" aria-hidden="true" />
          <Sun v-else-if="appStore.themeMode === 'light'" :size="14" aria-hidden="true" />
          <Moon v-else :size="14" aria-hidden="true" />
          <select
            :value="appStore.themeMode"
            aria-label="外观主题"
            @change="handleThemeChange"
          >
            <option value="system">跟随系统</option>
            <option value="light">浅色模式</option>
            <option value="dark">夜间模式</option>
          </select>
        </div>

        <a
          :href="appStore.authStatus.github_url"
          target="_blank"
          rel="noopener noreferrer"
          class="navbtn github-link"
          aria-label="访问 GitHub 项目主页"
          title="访问 GitHub 项目主页"
        >
          <svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>
          </svg>
          <span translate="no">GitHub</span>
        </a>

        <template v-if="!needsLogin">
          <button
            v-if="plannerEnabled && activeTab !== 'plan'"
            type="button"
            class="navbtn desktop-only"
            @click="activeTab = 'plan'"
          >
            升级路线规划 →
          </button>
          <button
            v-else-if="plannerEnabled && activeTab === 'plan'"
            type="button"
            class="navbtn desktop-only"
            @click="activeTab = 'store'"
          >
            ← 返回商店
          </button>

          <button
            v-if="isAdminUser()"
            type="button"
            class="navbtn"
            :disabled="refreshing"
            @click="handleManualRefresh"
          >
            <RefreshCw :size="14" :class="{ 'spin-icon': refreshing }" aria-hidden="true" />
            {{ refreshing ? "同步中…" : "立即同步" }}
          </button>

          <div
            v-if="appStore.authStatus.authenticated && appStore.authStatus.user"
            ref="accountMenuRef"
            class="account-menu"
          >
            <button
              type="button"
              class="navbtn account-trigger"
              aria-haspopup="menu"
              :aria-expanded="accountMenuOpen"
              @click="accountMenuOpen = !accountMenuOpen"
            >
              <Shield :size="14" aria-hidden="true" />
              <span>{{ appStore.authStatus.user.display_name || appStore.authStatus.user.username }}</span>
              <ChevronDown :size="13" aria-hidden="true" />
            </button>

            <Transition name="holo-pop">
              <div v-if="accountMenuOpen" class="account-popover" role="menu" aria-label="账号操作菜单">
                <div class="account-meta">
                  <strong>{{ appStore.authStatus.user.display_name || appStore.authStatus.user.username }}</strong>
                  <span>@{{ appStore.authStatus.user.username }} · {{ isAdminUser() ? "管理员" : "普通用户" }}</span>
                </div>
                <button
                  type="button"
                  class="account-item"
                  role="menuitem"
                  @click="openSwitchAccountModal"
                >
                  <UserCheck :size="14" aria-hidden="true" />
                  切换账号
                </button>
                <button
                  v-if="isAdminUser()"
                  type="button"
                  class="account-item"
                  role="menuitem"
                  @click="openSettingsSection('users', true)"
                >
                  <UserPlus :size="14" aria-hidden="true" />
                  添加账号
                </button>
                <button
                  type="button"
                  class="account-item"
                  role="menuitem"
                  @click="openAccountSettings"
                >
                  <KeyRound :size="14" aria-hidden="true" />
                  账户设置
                </button>
                <button
                  v-if="isAdminUser()"
                  type="button"
                  class="account-item"
                  role="menuitem"
                  @click="openSettingsSection('general')"
                >
                  <Settings :size="14" aria-hidden="true" />
                  系统设置
                </button>
                <button
                  type="button"
                  class="account-item danger"
                  role="menuitem"
                  @click="handleLogout"
                >
                  <LogOut :size="14" aria-hidden="true" />
                  退出登录
                </button>
              </div>
            </Transition>
          </div>

          <button
            v-else
            type="button"
            class="navbtn"
            @click="openSwitchAccountModal"
          >
            <UserCheck :size="14" aria-hidden="true" />
            登录 / 切换账号
          </button>
        </template>
      </div>
    </header>

    <!-- 未初始化或全站强制登录拦截图 -->
    <template v-if="!appStore.authLoaded">
      <div class="empty">正在检测系统身份与会话状态…</div>
    </template>

    <AuthView v-else-if="needsLogin" />

    <template v-else>
      <!-- 原版风格概要大标题（严格不显示已映射汉化数量） -->
      <section v-if="plannerEnabled && activeTab === 'plan'" class="lede">
        <h1>规划升级路线</h1>
        <p class="sub">
          结合当前全网在售 CCU 与你机库自备升级包，计算从你已有舰船升至目标船的最省钱连跳链条。
        </p>
      </section>

      <section v-else class="lede">
        <h1>
          <b>{{ inStoreCount }} 艘舰船</b>在售。<span>其中 {{ ledeStats.warbondCount }} 款单船含 <span translate="no">Warbond</span> 版本，{{ ledeStats.tenYearCount }} 款含 <span translate="no">10 Year</span> 版本，{{ ledeStats.ccuWarbondCount }} 个 <span translate="no">WBCCU</span><template v-if="ledeStats.discountedCount">，{{ ledeStats.discountedCount }} 款单船直降</template><template v-if="ledeStats.limitedCount">，{{ ledeStats.limitedCount }} 款限量</template>。</span>
        </h1>
      </section>

      <!-- 主导航标签栏（个人账户设置位于右侧独立分区） -->
      <nav class="tabs" aria-label="视图切换">
        <button
          v-for="tab in visibleTabs"
          :key="tab.id"
          type="button"
          :aria-pressed="activeTab === tab.id"
          @click="switchTab(tab.id)"
        >
          {{ tab.label }}
        </button>
        <button
          v-if="appStore.authStatus.authenticated"
          type="button"
          class="tab-account"
          :aria-pressed="activeTab === 'account'"
          @click="switchTab('account')"
        >
          <UserCog :size="14" aria-hidden="true" />
          账户设置
        </button>
      </nav>

      <main id="main-content" tabindex="-1">
        <StoreView
          v-show="activeTab === 'store' || activeTab === 'all'"
          :view-mode="activeStoreViewMode"
          @plan-route="handleOpenPlan"
        />
        <Transition name="holo-panel" mode="out-in">
          <ChangelogView v-if="activeTab === 'changes'" key="changes" />
          <UpgradePlannerView
            v-else-if="plannerEnabled && activeTab === 'plan'"
            key="plan"
            :initial-from-key="planFromKey"
            :initial-to-ref="planToRef"
          />
          <WebhooksView v-else-if="activeTab === 'webhooks' && isAdminUser()" key="webhooks" />
          <SettingsView
            v-else-if="activeTab === 'settings' && isAdminUser()"
            key="settings"
            :initial-sub-tab="settingsSubTab"
            :sub-tab-request-key="settingsSubTabKey"
            :open-create-user-signal="createUserSignal"
            @consume-create-user-signal="createUserSignal = 0"
          />
          <AccountSecurityPanel v-else-if="activeTab === 'account'" key="account" />
        </Transition>
      </main>
    </template>

    <!-- 切换账号弹窗（支持密码、OTP 与 ZITADEL PKCE SSO） -->
    <Transition name="holo-modal">
      <div v-if="switchModalOpen" class="modal-backdrop" @click.self="closeSwitchModal">
        <div
          class="modal-card compact"
          role="dialog"
          aria-modal="true"
          aria-labelledby="switch-account-title"
        >
          <div class="modal-head">
            <h3 id="switch-account-title">切换登录账号</h3>
            <button
              type="button"
              class="navbtn btn-icon"
              aria-label="关闭切换账号弹窗"
              @click="closeSwitchModal"
            >
              <X :size="15" aria-hidden="true" />
            </button>
          </div>

          <form v-if="!switchMfaToken" class="form-stack" @submit.prevent="submitSwitchAccount">
            <p v-if="appStore.authStatus.user" class="muted" style="margin: 0; font-size: 13px">
              当前账号：<b>{{ appStore.authStatus.user.username }}</b>，输入目标账号凭据即可直接切换。
            </p>
            <p v-if="switchError" class="auth-error" role="alert">{{ switchError }}</p>
            <label class="field-label">
              目标用户名或邮箱
              <input
                v-model="switchForm.username"
                name="switch_username"
                required
                autocomplete="username"
                spellcheck="false"
                placeholder="输入用户名或邮箱…"
                class="input"
              />
            </label>
            <label class="field-label">
              登录密码
              <input
                v-model="switchForm.password"
                name="switch_password"
                type="password"
                required
                autocomplete="current-password"
                class="input"
              />
            </label>
            <div class="modal-foot">
              <button type="button" class="navbtn" @click="closeSwitchModal">取消</button>
              <button type="submit" class="navbtn primary" :disabled="switchSubmitting">
                {{ switchSubmitting ? "切换中…" : "确认切换" }}
              </button>
            </div>
            <div v-if="appStore.authStatus.sso_enabled || passkeySwitchUsable" class="auth-divider"><span>或</span></div>
            <button
              v-if="passkeySwitchUsable"
              type="button"
              class="navbtn btn-full"
              :disabled="switchSubmitting"
              @click="startSwitchPasskey"
            >
              <Fingerprint :size="15" aria-hidden="true" />
              使用 Passkey 通行密钥切换
            </button>
            <button
              v-if="appStore.authStatus.sso_enabled"
              type="button"
              class="navbtn btn-full"
              @click="startSwitchSso"
            >
              使用 {{ appStore.authStatus.sso_provider_name || "OIDC" }} 登录切换
            </button>
          </form>

          <form v-else class="form-stack" @submit.prevent="submitSwitchOtp">
            <p v-if="switchError" class="auth-error" role="alert">{{ switchError }}</p>
            <p class="muted" style="margin: 0; font-size: 13px">
              目标账号已启用二步验证，请输入 6 位 OTP 动态口令或 8 位备用恢复码。
            </p>
            <label class="field-label">
              OTP 动态口令 / 备用恢复码
              <input
                v-model="switchForm.otpCode"
                name="switch_otp_code"
                required
                autocomplete="one-time-code"
                spellcheck="false"
                maxlength="20"
                placeholder="6 位数字或 XXXX-XXXX"
                class="input mono"
              />
            </label>
            <div class="modal-foot">
              <button type="button" class="navbtn" @click="switchMfaToken = ''">返回上一步</button>
              <button type="submit" class="navbtn primary" :disabled="switchSubmitting">
                {{ switchSubmitting ? "验证中…" : "验证并切换" }}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Transition>

    <footer>
      <p>
        <a
          :href="appStore.authStatus.github_url"
          target="_blank"
          rel="noopener noreferrer"
          translate="no"
        >CCUJailer v{{ appStore.authStatus.app_version }}</a>（<a
          :href="appStore.authStatus.github_url"
          target="_blank"
          rel="noopener noreferrer"
        >GitHub 项目主页 ↗</a>）是基于 <a href="https://github.com/DieLaughing/pledgewatch" target="_blank" rel="noopener">Pledge Watch</a>（AGPL-3.0）构建的非官方开源工具，与 Cloud Imperium Games 或 Roberts Space Industries 无关。价格取自 RSI 公开商店税前美元售价；货舱布局源自 <a href="https://sc-cargo.space" target="_blank" rel="noopener">sc-cargo.space</a>（MIT）；游戏内售价与租金源自 <a href="https://uexcorp.space" target="_blank" rel="noopener">UEX</a>；武器火力、保险索赔耗时、历史价格与售卖规律源自 <a href="https://starcitizen.tools" target="_blank" rel="noopener">Star Citizen Wiki</a>（CC BY-SA 4.0）<template v-if="appStore.storeData.game_version">（游戏版本 {{ appStore.storeData.game_version }}）</template>。
      </p>
    </footer>

    <!-- 全局轻提示 -->
    <TransitionGroup
      name="holo-toast"
      tag="div"
      class="toast-stack"
      role="status"
      aria-live="polite"
      aria-atomic="false"
    >
      <div
        v-for="toast in appStore.toasts"
        :key="toast.id"
        class="toast"
        :class="toast.kind"
      >
        <span>{{ toast.text }}</span>
        <button
          type="button"
          class="toast-close"
          aria-label="关闭提示"
          @click="dismissToast(toast.id)"
        >
          <X :size="14" aria-hidden="true" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>
