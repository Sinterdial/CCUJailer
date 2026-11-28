<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import {
  Check,
  Copy,
  Download,
  Fingerprint,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-vue-next";
import { apiClient, setSavedSessionToken } from "../api/client";
import {
  DEFAULT_STORE_PAGE_SIZE,
  appStore,
  formatUtcTime,
  isAdminUser,
  notify,
  refreshBootstrapState,
  setLanguageMode,
  setThemeMode,
  updateUserPreferences,
  type ThemeMode,
} from "../composables/useAppStore";
import type { AuthUser, LanguageMode, PasskeyItem, TotpSetupResponse } from "../types/api";
import { STORE_FILTER_META, STORE_SORT_OPTIONS } from "../utils/localization";
import { createPasskeyCredential, isPasskeyOriginUsable } from "../utils/webauthn";

const loading = ref(false);
const profile = ref<AuthUser | null>(null);
const savingPass = ref(false);

// TOTP 与一次性备用恢复码状态
const totpSetupData = ref<TotpSetupResponse | null>(null);
const totpConfirmCode = ref("");
const totpBusy = ref(false);
const generatedBackupCodes = ref<string[]>([]);
const regenOtpCode = ref("");
const regenPassword = ref("");
const showRegenForm = ref(false);
const disableCode = ref("");
const disablePass = ref("");

// Passkey 个人凭据与全站配置状态
const passkeys = ref<PasskeyItem[]>([]);
const passkeysLoading = ref(false);
const passkeyBusy = ref(false);
const newPasskeyName = ref("");
const editingPasskeyId = ref<number | null>(null);
const editingPasskeyName = ref("");
const passkeyOriginCheck = computed(() => isPasskeyOriginUsable());
const adminPasskeySaving = ref(false);
const adminPasskeyForm = reactive({
  passkey_enabled: true,
  passkey_rp_name: "CCUJailer",
  passkey_rp_id: "",
  passkey_origin: "",
});

// 个人偏好（跨设备同步）
const prefSaving = ref(false);
const PAGE_SIZE_OPTIONS: { value: number; label: string }[] = [
  { value: 10, label: "每页 10 艘" },
  { value: 20, label: "每页 20 艘（默认）" },
  { value: 30, label: "每页 30 艘" },
  { value: 50, label: "每页 50 艘" },
  { value: 100, label: "每页 100 艘" },
  { value: 0, label: "全部显示" },
];

const prefForm = reactive({
  language_mode: appStore.languageMode as LanguageMode,
  theme_mode: appStore.themeMode as ThemeMode,
  auec_rate: appStore.userPreferences.auec_rate || 500000,
  store_page_size:
    typeof appStore.userPreferences.store_page_size === "number"
      ? appStore.userPreferences.store_page_size
      : DEFAULT_STORE_PAGE_SIZE,
  inherit_store_defaults: appStore.userPreferences.default_store_filters == null,
  default_store_filters: [...(appStore.userPreferences.default_store_filters || [])] as string[],
  default_store_sort:
    appStore.userPreferences.default_store_sort ||
    appStore.authStatus.default_store_sort ||
    "price-desc",
});

const passForm = reactive({
  currentPassword: "",
  newPassword: "",
  confirmPassword: "",
});

function syncPrefFormFromStore(): void {
  prefForm.language_mode = appStore.languageMode;
  prefForm.theme_mode = appStore.themeMode;
  prefForm.auec_rate = appStore.userPreferences.auec_rate || 500000;
  prefForm.store_page_size =
    typeof appStore.userPreferences.store_page_size === "number"
      ? appStore.userPreferences.store_page_size
      : DEFAULT_STORE_PAGE_SIZE;
  const hasCustom = Array.isArray(appStore.userPreferences.default_store_filters);
  prefForm.inherit_store_defaults = !hasCustom;
  prefForm.default_store_filters = hasCustom
    ? [...(appStore.userPreferences.default_store_filters || [])]
    : [...(appStore.authStatus.default_store_filters || [])];
  prefForm.default_store_sort =
    appStore.userPreferences.default_store_sort ||
    appStore.authStatus.default_store_sort ||
    "price-desc";
}

watch(
  () => appStore.preferencesSyncVersion,
  () => {
    syncPrefFormFromStore();
  }
);

function togglePersonalDefaultFilter(key: string): void {
  if (prefForm.inherit_store_defaults) {
    prefForm.inherit_store_defaults = false;
  }
  const idx = prefForm.default_store_filters.indexOf(key);
  if (idx === -1) {
    prefForm.default_store_filters.push(key);
  } else {
    prefForm.default_store_filters.splice(idx, 1);
  }
}

async function savePersonalPreferences(): Promise<void> {
  prefSaving.value = true;
  try {
    setLanguageMode(prefForm.language_mode, false);
    setThemeMode(prefForm.theme_mode, false);
    const rate = Math.max(1000, Math.round(Number(prefForm.auec_rate) || 500000));
    await updateUserPreferences(
      {
        language_mode: prefForm.language_mode,
        theme_mode: prefForm.theme_mode,
        auec_rate: rate,
        store_page_size: prefForm.store_page_size,
        default_store_filters: prefForm.inherit_store_defaults
          ? null
          : [...prefForm.default_store_filters],
        default_store_sort: prefForm.inherit_store_defaults ? null : prefForm.default_store_sort,
      },
      { immediate: true }
    );
    await refreshBootstrapState();
    notify("个人偏好已保存并同步", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存个人偏好失败", "error");
  } finally {
    prefSaving.value = false;
  }
}

async function loadPasskeys(): Promise<void> {
  if (!profile.value || profile.value.id <= 0) {
    return;
  }
  passkeysLoading.value = true;
  try {
    const res = await apiClient.listPasskeys();
    passkeys.value = res.items;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载 Passkey 列表失败", "error");
  } finally {
    passkeysLoading.value = false;
  }
}

async function loadAdminPasskeyConfig(): Promise<void> {
  if (!isAdminUser()) {
    return;
  }
  try {
    const cfg = await apiClient.getSettings();
    adminPasskeyForm.passkey_enabled = cfg.passkey_enabled;
    adminPasskeyForm.passkey_rp_name = cfg.passkey_rp_name || "CCUJailer";
    adminPasskeyForm.passkey_rp_id = cfg.passkey_rp_id || "";
    adminPasskeyForm.passkey_origin = cfg.passkey_origin || "";
  } catch {
    // 忽略读取异常
  }
}

function fillCurrentOriginForPasskey(): void {
  const host = window.location.hostname || "";
  const origin = window.location.origin || "";
  if (host) {
    adminPasskeyForm.passkey_rp_id = host;
  }
  if (origin) {
    adminPasskeyForm.passkey_origin = origin;
  }
}

async function saveAdminPasskeyConfig(): Promise<void> {
  if (!isAdminUser()) {
    return;
  }
  adminPasskeySaving.value = true;
  try {
    await apiClient.updateSettings({
      passkey_enabled: adminPasskeyForm.passkey_enabled,
      passkey_rp_name: adminPasskeyForm.passkey_rp_name.trim() || "CCUJailer",
      passkey_rp_id: adminPasskeyForm.passkey_rp_id.trim(),
      passkey_origin: adminPasskeyForm.passkey_origin.trim(),
    });
    await refreshBootstrapState();
    notify("全站 Passkey 参数已更新并生效", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存 Passkey 配置失败", "error");
  } finally {
    adminPasskeySaving.value = false;
  }
}

async function handleRegisterPasskey(): Promise<void> {
  const originCheck = isPasskeyOriginUsable();
  if (!originCheck.ok) {
    notify(originCheck.reason, "error");
    return;
  }
  passkeyBusy.value = true;
  try {
    const challenge = await apiClient.beginPasskeyRegister();
    const credential = await createPasskeyCredential(challenge.options);
    await apiClient.verifyPasskeyRegister({
      challenge_id: challenge.challenge_id,
      name: newPasskeyName.value.trim(),
      credential,
    });
    newPasskeyName.value = "";
    await Promise.all([loadPasskeys(), loadProfile(), refreshBootstrapState()]);
    notify("已成功绑定新的 Passkey 通行密钥", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "注册 Passkey 失败", "error");
  } finally {
    passkeyBusy.value = false;
  }
}

function startRenamePasskey(item: PasskeyItem): void {
  editingPasskeyId.value = item.id;
  editingPasskeyName.value = item.name;
}

function cancelRenamePasskey(): void {
  editingPasskeyId.value = null;
  editingPasskeyName.value = "";
}

async function saveRenamePasskey(item: PasskeyItem): Promise<void> {
  const clean = editingPasskeyName.value.trim();
  if (!clean) {
    notify("Passkey 名称不能为空", "error");
    return;
  }
  passkeyBusy.value = true;
  try {
    await apiClient.renamePasskey(item.id, clean);
    editingPasskeyId.value = null;
    await loadPasskeys();
    notify("已更新 Passkey 名称", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "重命名 Passkey 失败", "error");
  } finally {
    passkeyBusy.value = false;
  }
}

async function handleDeletePasskey(item: PasskeyItem): Promise<void> {
  if (!window.confirm(`确定移除通行密钥「${item.name}」吗？移除后该设备将无法再用于免密登录。`)) {
    return;
  }
  passkeyBusy.value = true;
  try {
    await apiClient.deletePasskey(item.id);
    await Promise.all([loadPasskeys(), loadProfile(), refreshBootstrapState()]);
    notify("已移除 Passkey 通行密钥", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "移除 Passkey 失败", "error");
  } finally {
    passkeyBusy.value = false;
  }
}

async function loadProfile(): Promise<void> {
  loading.value = true;
  try {
    profile.value = await apiClient.getMe();
    syncPrefFormFromStore();
    if (profile.value && profile.value.id > 0) {
      await Promise.all([loadPasskeys(), loadAdminPasskeyConfig()]);
    }
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载账号信息失败", "error");
  } finally {
    loading.value = false;
  }
}

async function submitPasswordChange(): Promise<void> {
  if (passForm.newPassword !== passForm.confirmPassword) {
    notify("两次输入的新密码不一致", "error");
    return;
  }
  savingPass.value = true;
  try {
    const res = await apiClient.changePassword({
      current_password: passForm.currentPassword,
      new_password: passForm.newPassword,
    });
    if (res.access_token) {
      setSavedSessionToken(res.access_token);
    }
    passForm.currentPassword = "";
    passForm.newPassword = "";
    passForm.confirmPassword = "";
    await Promise.all([loadProfile(), refreshBootstrapState()]);
    notify("密码修改成功（旧设备登录会话已自动失效）", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "修改密码失败", "error");
  } finally {
    savingPass.value = false;
  }
}

async function beginTotpSetup(): Promise<void> {
  totpBusy.value = true;
  try {
    totpSetupData.value = await apiClient.setupTotp();
    totpConfirmCode.value = "";
  } catch (err) {
    notify(err instanceof Error ? err.message : "生成 OTP 绑定二维码失败", "error");
  } finally {
    totpBusy.value = false;
  }
}

async function confirmEnableTotp(): Promise<void> {
  totpBusy.value = true;
  try {
    const res = await apiClient.enableTotp(totpConfirmCode.value.trim());
    profile.value = res.user;
    generatedBackupCodes.value = res.backup_codes || [];
    totpSetupData.value = null;
    totpConfirmCode.value = "";
    await refreshBootstrapState();
    notify("OTP 二步验证已开启，请务必妥善保存下方一次性备用恢复码", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "OTP 验证失败", "error");
  } finally {
    totpBusy.value = false;
  }
}

async function confirmRegenerateBackupCodes(): Promise<void> {
  totpBusy.value = true;
  try {
    const res = await apiClient.regenerateTotpBackupCodes({
      otp_code: regenOtpCode.value.trim(),
      current_password: regenPassword.value,
    });
    generatedBackupCodes.value = res.backup_codes || [];
    regenOtpCode.value = "";
    regenPassword.value = "";
    showRegenForm.value = false;
    await Promise.all([loadProfile(), refreshBootstrapState()]);
    notify("已重新生成 8 个一次性备用恢复码，旧备用码已全部作废", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "重新生成备用恢复码失败", "error");
  } finally {
    totpBusy.value = false;
  }
}

async function copyBackupCodes(): Promise<void> {
  if (!generatedBackupCodes.value.length) {
    return;
  }
  try {
    await navigator.clipboard.writeText(generatedBackupCodes.value.join("\n"));
    notify("已复制全部备用恢复码到剪贴板", "success");
  } catch {
    notify("复制失败，请手动复制或点击下载保存", "info");
  }
}

function downloadBackupCodes(): void {
  if (!generatedBackupCodes.value.length) {
    return;
  }
  const content = [
    `CCUJailer TOTP 一次性备用恢复码（账号：${profile.value?.username || ""}）`,
    "每个备用码仅可使用一次，请勿泄露给他人。",
    "",
    ...generatedBackupCodes.value,
    "",
  ].join("\n");
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `ccujailer-${profile.value?.username || "account"}-backup-codes.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

async function confirmDisableTotp(): Promise<void> {
  totpBusy.value = true;
  try {
    profile.value = await apiClient.disableTotp({
      otp_code: disableCode.value.trim(),
      current_password: disablePass.value,
    });
    disableCode.value = "";
    disablePass.value = "";
    generatedBackupCodes.value = [];
    showRegenForm.value = false;
    await refreshBootstrapState();
    notify("已关闭 OTP 二步验证", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "解绑 OTP 失败", "error");
  } finally {
    totpBusy.value = false;
  }
}

onMounted(() => {
  void loadProfile();
});
</script>

<template>
  <div class="settings-section">
    <div v-if="loading && !profile" class="panel muted panel-loading">
      加载账户设置中…
    </div>

    <template v-else-if="profile">
      <!-- 1. 安全与隐私（合并账号密码安全、TOTP 与备用码、Passkey） -->
      <div class="panel form-stack">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">安全与隐私</h2>
            <p class="panel-sub">
              当前账号：<strong style="color: var(--fg)">{{ profile.username }}</strong>
              <span class="pill info pill-inline">
                {{ profile.role === "admin" ? "管理员" : "普通用户" }}
              </span>
              <span v-if="profile.oidc_bound" class="pill good pill-inline">
                已绑定 OIDC
              </span>
            </p>
          </div>
        </div>

        <!-- 1.1 登录密码管理 -->
        <form class="sub-box stack-md" @submit.prevent="submitPasswordChange">
          <div class="row-between">
            <strong style="font-size: 14px">登录密码管理</strong>
          </div>

          <div class="form-grid-2">
            <label v-if="profile.has_password" class="field-label">
              当前登录密码
              <input
                v-model="passForm.currentPassword"
                name="current_password"
                type="password"
                required
                autocomplete="current-password"
                class="input"
              />
            </label>
            <div v-else class="field-label">
              当前密码状态
              <div class="input muted">OIDC 账号暂未设置本地密码（下方可直接设置）</div>
            </div>
            <div aria-hidden="true" />

            <label class="field-label">
              新密码（至少 8 位）
              <input
                v-model="passForm.newPassword"
                name="new_password"
                type="password"
                required
                minlength="8"
                autocomplete="new-password"
                class="input"
              />
            </label>
            <label class="field-label">
              确认新密码
              <input
                v-model="passForm.confirmPassword"
                name="confirm_password"
                type="password"
                required
                minlength="8"
                autocomplete="new-password"
                class="input"
              />
            </label>
          </div>

          <div class="row-end">
            <button type="submit" class="navbtn primary" :disabled="savingPass">
              <KeyRound :size="14" aria-hidden="true" />
              {{ savingPass ? "保存中…" : profile.has_password ? "更新密码" : "设置本地密码" }}
            </button>
          </div>
        </form>

        <!-- 1.2 TOTP 动态口令与备用恢复码 -->
        <div class="sub-box stack-md">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">TOTP 动态口令（二步验证与备用恢复码）</strong>
              <p class="panel-sub">
                使用 Google Authenticator、1Password、Bitwarden 等验证器保护登录，绑定后自动生成 8 个一次性备用恢复码以防设备丢失。
              </p>
            </div>
            <div class="badge-group">
              <span class="pill" :class="profile.totp_enabled ? 'good' : 'neutral'">
                {{ profile.totp_enabled ? "已启用 2FA" : "未开启" }}
              </span>
              <span
                v-if="profile.totp_enabled"
                class="pill"
                :class="(profile.totp_backup_codes_remaining || 0) > 2 ? 'info' : 'warn'"
              >
                剩余备用码 {{ profile.totp_backup_codes_remaining ?? 0 }} 个
              </span>
            </div>
          </div>

          <!-- 刚生成的一次性备用恢复码展示框 -->
          <Transition name="holo-expand">
            <div v-if="generatedBackupCodes.length" class="backup-codes-box" role="region" aria-label="TOTP 备用恢复码">
              <div class="row-between">
                <div>
                  <strong style="font-size: 14px">请立即保存你的一次性备用恢复码</strong>
                  <p class="panel-sub">
                    以下 8 个恢复码仅展示这一次；若验证器设备丢失，任一备用码可代替 6 位动态口令登录（每个仅可用一次）。
                  </p>
                </div>
                <div class="inline-actions">
                  <button type="button" class="navbtn btn-sm" @click="copyBackupCodes">
                    <Copy :size="13" aria-hidden="true" />
                    复制全部
                  </button>
                  <button type="button" class="navbtn btn-sm" @click="downloadBackupCodes">
                    <Download :size="13" aria-hidden="true" />
                    下载 .txt
                  </button>
                  <button
                    type="button"
                    class="navbtn btn-sm"
                    @click="generatedBackupCodes = []"
                  >
                    我已妥善保存
                  </button>
                </div>
              </div>
              <div class="backup-codes-grid">
                <code
                  v-for="code in generatedBackupCodes"
                  :key="code"
                  class="backup-code-item mono"
                  translate="no"
                >
                  {{ code }}
                </code>
              </div>
            </div>
          </Transition>

          <div v-if="profile.id === 0" class="muted cell-meta">
            当前使用静态 <span class="mono" translate="no">ADMIN_TOKEN</span> 兼容会话登录，不支持配置 OTP。请使用真实账号登录操作。
          </div>

          <template v-else-if="!profile.totp_enabled">
            <Transition name="holo-expand" mode="out-in">
              <div v-if="!totpSetupData" key="totp-idle">
                <button
                  type="button"
                  class="navbtn primary"
                  :disabled="totpBusy"
                  @click="beginTotpSetup"
                >
                  <ShieldCheck :size="14" aria-hidden="true" />
                  {{ totpBusy ? "生成中…" : "生成绑定二维码" }}
                </button>
              </div>

              <div v-else key="totp-qr" class="stack-md">
                <div class="qr-layout">
                  <div class="qr-box" aria-label="TOTP 二维码" v-html="totpSetupData.qr_svg" />
                  <div class="qr-meta">
                    <strong style="font-size: 14px">1. 使用验证器 App 扫描左侧二维码</strong>
                    <p class="panel-sub">无法扫码时，请手动输入以下 Base32 密钥：</p>
                    <code class="mono qr-secret" translate="no">{{ totpSetupData.secret }}</code>
                  </div>
                </div>

                <form class="otp-confirm-form" @submit.prevent="confirmEnableTotp">
                  <label class="field-label">
                    2. 输入验证器上生成的 6 位数字口令以完成绑定并生成备用码
                    <input
                      v-model="totpConfirmCode"
                      name="totp_confirm_code"
                      required
                      maxlength="8"
                      inputmode="numeric"
                      autocomplete="one-time-code"
                      spellcheck="false"
                      placeholder="000000"
                      class="input mono"
                    />
                  </label>
                  <button type="button" class="navbtn" @click="totpSetupData = null">取消</button>
                  <button type="submit" class="navbtn primary" :disabled="totpBusy">
                    <ShieldCheck :size="14" aria-hidden="true" />
                    验证并开启 OTP
                  </button>
                </form>
              </div>
            </Transition>
          </template>

          <template v-else>
            <div class="inline-actions">
              <button
                type="button"
                class="navbtn"
                @click="showRegenForm = !showRegenForm"
              >
                <RefreshCw :size="13" aria-hidden="true" />
                {{ showRegenForm ? "收起重新生成备用码" : "重新生成备用恢复码" }}
              </button>
            </div>

            <Transition name="holo-expand">
              <form
                v-if="showRegenForm"
                class="panel stack-sm"
                @submit.prevent="confirmRegenerateBackupCodes"
              >
              <strong style="font-size: 13px">核验身份以生成全新 8 个备用码（旧备用码将作废）</strong>
              <div class="form-grid-2">
                <label class="field-label">
                  6 位动态口令（优先）
                  <input
                    v-model="regenOtpCode"
                    maxlength="12"
                    inputmode="numeric"
                    autocomplete="one-time-code"
                    placeholder="例如 123456"
                    class="input mono"
                  />
                </label>
                <label class="field-label">
                  或当前密码
                  <input
                    v-model="regenPassword"
                    type="password"
                    autocomplete="current-password"
                    placeholder="输入登录密码"
                    class="input"
                  />
                </label>
              </div>
                <div class="row-end">
                  <button type="submit" class="navbtn primary btn-sm" :disabled="totpBusy">
                    确认重新生成
                  </button>
                </div>
              </form>
            </Transition>

            <form class="stack-sm" @submit.prevent="confirmDisableTotp">
              <strong style="font-size: 13px; color: var(--fg)">关闭 / 解绑 OTP 二步验证</strong>
              <p class="panel-sub">输入当前 6 位 OTP 口令、备用恢复码或您的账号登录密码即可解除绑定。</p>
              <div class="form-grid-2">
                <label class="field-label">
                  动态口令或备用恢复码（优先）
                  <input
                    v-model="disableCode"
                    name="disable_otp_code"
                    maxlength="20"
                    autocomplete="one-time-code"
                    spellcheck="false"
                    placeholder="例如 123456 或 XXXX-XXXX"
                    class="input mono"
                  />
                </label>
                <label class="field-label">
                  或当前密码
                  <input
                    v-model="disablePass"
                    name="disable_current_password"
                    type="password"
                    autocomplete="current-password"
                    placeholder="没有验证器时输入密码"
                    class="input"
                  />
                </label>
              </div>
              <div class="row-end">
                <button type="submit" class="navbtn danger" :disabled="totpBusy">
                  <ShieldAlert :size="14" aria-hidden="true" />
                  {{ totpBusy ? "处理中…" : "停用 OTP" }}
                </button>
              </div>
            </form>
          </template>
        </div>

        <!-- 1.3 Passkey 通行密钥（含管理员全局参数配置） -->
        <div class="sub-box stack-md">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">Passkey 通行密钥（无密码登录）</strong>
              <p class="panel-sub">
                绑定指纹、面容 ID、Windows Hello 或硬件安全密钥后，可在登录页免密码一键登录。
              </p>
            </div>
            <span class="pill" :class="passkeys.length > 0 ? 'good' : 'neutral'">
              已绑定 {{ passkeys.length }} 个
            </span>
          </div>

          <div v-if="profile.id === 0" class="muted cell-meta">
            静态 <span class="mono" translate="no">ADMIN_TOKEN</span> 兼容会话不支持管理 Passkey。请使用真实账号登录操作。
          </div>

          <template v-else>
            <p v-if="!appStore.authStatus.passkey_enabled" class="passkey-warn" role="status">
              全站 Passkey 登录开关当前处于关闭状态，你可以预先管理设备密钥或由管理员在下方开启。
            </p>
            <p v-else-if="!passkeyOriginCheck.ok" class="passkey-warn" role="status">
              {{ passkeyOriginCheck.reason }}
            </p>

            <form class="passkey-add-row" @submit.prevent="handleRegisterPasskey">
              <input
                v-model="newPasskeyName"
                type="text"
                maxlength="120"
                placeholder="输入设备备注名称（可选，如 MacBook TouchID）"
                class="input"
              />
              <button
                type="submit"
                class="navbtn primary"
                :disabled="passkeyBusy || !passkeyOriginCheck.ok"
              >
                <Fingerprint :size="14" aria-hidden="true" />
                <Plus :size="14" aria-hidden="true" />
                {{ passkeyBusy ? "请在设备确认…" : "添加 Passkey 通行密钥" }}
              </button>
            </form>

            <div v-if="passkeysLoading && passkeys.length === 0" class="muted cell-meta">
              正在加载 Passkey 凭据列表…
            </div>

            <div v-else-if="passkeys.length === 0" class="empty">
              尚未绑定任何 Passkey 通行密钥，点击上方按钮即可为当前设备添加。
            </div>

            <ul v-else class="passkey-list">
              <li v-for="pk in passkeys" :key="pk.id" class="passkey-row">
                <div class="passkey-meta">
                  <template v-if="editingPasskeyId === pk.id">
                    <div class="inline-edit-row">
                      <input
                        v-model="editingPasskeyName"
                        type="text"
                        maxlength="120"
                        class="input input-compact"
                        aria-label="Passkey 设备名称"
                        @keyup.enter="saveRenamePasskey(pk)"
                        @keyup.esc="cancelRenamePasskey"
                      />
                      <button
                        type="button"
                        class="navbtn primary btn-icon"
                        :disabled="passkeyBusy"
                        aria-label="保存名称"
                        @click="saveRenamePasskey(pk)"
                      >
                        <Check :size="13" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        class="navbtn btn-icon"
                        aria-label="取消重命名"
                        @click="cancelRenamePasskey"
                      >
                        <X :size="13" aria-hidden="true" />
                      </button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="badge-group">
                      <strong>{{ pk.name }}</strong>
                      <span v-if="pk.backed_up" class="pill info">多端同步</span>
                      <span v-for="t in pk.transports" :key="t" class="pill neutral mono">{{ t }}</span>
                    </div>
                    <div class="muted cell-meta">
                      创建于 {{ formatUtcTime(pk.created_at) }}
                      <template v-if="pk.last_used_at">
                        · 最近使用 {{ formatUtcTime(pk.last_used_at) }}
                      </template>
                    </div>
                  </template>
                </div>

                <div v-if="editingPasskeyId !== pk.id" class="inline-actions">
                  <button
                    type="button"
                    class="navbtn btn-sm"
                    :disabled="passkeyBusy"
                    @click="startRenamePasskey(pk)"
                  >
                    <Pencil :size="12" aria-hidden="true" />
                    重命名
                  </button>
                  <button
                    type="button"
                    class="navbtn danger btn-sm"
                    :disabled="passkeyBusy"
                    @click="handleDeletePasskey(pk)"
                  >
                    <Trash2 :size="12" aria-hidden="true" />
                    删除
                  </button>
                </div>
              </li>
            </ul>

            <!-- 管理员可见：全站 Passkey 参数设置 -->
            <form
              v-if="isAdminUser()"
              class="panel stack-md"
              @submit.prevent="saveAdminPasskeyConfig"
            >
              <div class="row-between">
                <div>
                  <strong style="font-size: 13px">全站 Passkey（WebAuthn）参数配置 · 管理员</strong>
                  <p class="panel-sub">
                    控制全站 Passkey 登录开关及 RP ID / Origin 校验规则（留空则按请求域名自动推断，不支持纯 IP）。
                  </p>
                </div>
                <div class="inline-actions">
                  <button type="button" class="navbtn btn-sm" @click="fillCurrentOriginForPasskey">
                    填入当前域名与 Origin
                  </button>
                  <label class="inline-check">
                    <input v-model="adminPasskeyForm.passkey_enabled" type="checkbox" />
                    启用 Passkey 登录
                  </label>
                </div>
              </div>

              <div class="form-grid-3">
                <label class="field-label">
                  RP 显示名称
                  <input
                    v-model="adminPasskeyForm.passkey_rp_name"
                    name="passkey_rp_name"
                    placeholder="CCUJailer"
                    class="input"
                  />
                </label>
                <label class="field-label">
                  RP ID（域名）
                  <input
                    v-model="adminPasskeyForm.passkey_rp_id"
                    name="passkey_rp_id"
                    spellcheck="false"
                    placeholder="留空自动推断"
                    class="input mono"
                  />
                </label>
                <label class="field-label">
                  允许 Origin（逗号分隔）
                  <input
                    v-model="adminPasskeyForm.passkey_origin"
                    name="passkey_origin"
                    spellcheck="false"
                    placeholder="留空自动推断"
                    class="input mono"
                  />
                </label>
              </div>

              <div class="row-end">
                <button type="submit" class="navbtn primary btn-sm" :disabled="adminPasskeySaving">
                  <Save :size="13" aria-hidden="true" />
                  {{ adminPasskeySaving ? "保存中…" : "保存 Passkey 参数" }}
                </button>
              </div>
            </form>
          </template>
        </div>
      </div>

      <!-- 2. 个人偏好（跨终端同步） -->
      <form
        v-if="profile.id > 0"
        class="panel form-stack"
        @submit.prevent="savePersonalPreferences"
      >
        <div class="panel-header">
          <div>
            <h2 class="panel-title">个人偏好</h2>
            <p class="panel-sub">
              语言、主题、aUEC/h 时薪、每页显示舰船数量、详情卡片排序/折叠、已囤 CCU 及首页默认筛选均保存至当前账号，跨浏览器与多终端自动同步。
            </p>
          </div>
          <span class="pill good">已开启云端同步</span>
        </div>

        <div class="form-grid-2">
          <label class="field-label">
            默认语言展示
            <select v-model="prefForm.language_mode" class="select">
              <option value="bilingual">中英双语</option>
              <option value="zh">仅中文</option>
              <option value="en">仅英文</option>
            </select>
          </label>
          <label class="field-label">
            外观主题模式
            <select v-model="prefForm.theme_mode" class="select">
              <option value="system">跟随系统</option>
              <option value="light">浅色模式</option>
              <option value="dark">深色模式</option>
            </select>
          </label>
          <label class="field-label">
            游戏内每小时收入 (aUEC/h)
            <input
              v-model.number="prefForm.auec_rate"
              type="number"
              min="1000"
              max="100000000"
              class="input mono"
            />
          </label>
          <label class="field-label">
            舰船列表每页最大显示数量
            <select v-model.number="prefForm.store_page_size" class="select">
              <option v-for="opt in PAGE_SIZE_OPTIONS" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
        </div>

        <div class="sub-box stack-md">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">我的首页（在售舰船）默认筛选与排序</strong>
              <p class="panel-sub">
                可选择跟随管理员配置的全站默认条件，或为自己固定常用条件（如「仅 WBCCU」）。
              </p>
            </div>
            <label class="inline-check">
              <input v-model="prefForm.inherit_store_defaults" type="checkbox" />
              跟随系统默认筛选
            </label>
          </div>

          <Transition name="holo-expand">
            <div v-if="!prefForm.inherit_store_defaults" class="stack-md">
              <div class="filter-check-grid" role="group" aria-label="个人默认首页筛选条件">
                <button
                  v-for="item in STORE_FILTER_META"
                  :key="item.key"
                  type="button"
                  class="chip"
                  :aria-pressed="prefForm.default_store_filters.includes(item.key)"
                  @click="togglePersonalDefaultFilter(item.key)"
                >
                  {{ item.label }}
                </button>
              </div>

              <label class="field-label" style="max-width: 320px">
                个人首页默认排序
                <select v-model="prefForm.default_store_sort" class="select">
                  <option v-for="opt in STORE_SORT_OPTIONS" :key="opt.key" :value="opt.key">
                    {{ opt.label }}
                  </option>
                </select>
              </label>
            </div>
          </Transition>
        </div>

        <div class="row-end">
          <button type="submit" class="navbtn primary" :disabled="prefSaving">
            <Save :size="14" aria-hidden="true" />
            {{ prefSaving ? "同步中…" : "保存个人偏好" }}
          </button>
        </div>
      </form>
    </template>
  </div>
</template>
