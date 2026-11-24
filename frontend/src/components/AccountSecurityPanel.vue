<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { KeyRound, ShieldAlert, ShieldCheck } from "lucide-vue-next";
import { apiClient, setSavedSessionToken } from "../api/client";
import { notify, refreshBootstrapState } from "../composables/useAppStore";
import type { AuthUser, TotpSetupResponse } from "../types/api";

const loading = ref(false);
const profile = ref<AuthUser | null>(null);
const savingPass = ref(false);
const totpSetupData = ref<TotpSetupResponse | null>(null);
const totpConfirmCode = ref("");
const totpBusy = ref(false);
const disableCode = ref("");
const disablePass = ref("");

const passForm = reactive({
  currentPassword: "",
  newPassword: "",
  confirmPassword: "",
});

async function loadProfile(): Promise<void> {
  loading.value = true;
  try {
    profile.value = await apiClient.getMe();
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
    profile.value = await apiClient.enableTotp(totpConfirmCode.value.trim());
    totpSetupData.value = null;
    totpConfirmCode.value = "";
    await refreshBootstrapState();
    notify("OTP 二步验证已开启", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "OTP 验证失败", "error");
  } finally {
    totpBusy.value = false;
  }
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
      加载账号安全信息中…
    </div>

    <template v-else-if="profile">
      <!-- 基本信息与改密 -->
      <form class="panel form-stack" @submit.prevent="submitPasswordChange">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">个人账号与密码安全</h2>
            <p class="panel-sub">
              当前账号：<strong style="color: var(--fg)">{{ profile.username }}</strong>
              <span class="pill info pill-inline">
                {{ profile.role === "admin" ? "管理员" : "普通用户" }}
              </span>
              <span v-if="profile.oidc_bound" class="pill good pill-inline">
                已绑定 SSO
              </span>
            </p>
          </div>
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
            <div class="input muted">SSO 注册账号，暂未设置本地密码（下方可直接设置）</div>
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

      <!-- TOTP 二步验证管理 -->
      <div class="panel form-stack">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">TOTP 动态口令（OTP 二步验证）</h2>
            <p class="panel-sub">
              使用 Google Authenticator、1Password、Bitwarden 或 Microsoft Authenticator 保护本地账号密码登录。
            </p>
          </div>
          <span class="pill" :class="profile.totp_enabled ? 'good' : 'neutral'">
            {{ profile.totp_enabled ? "已启用 2FA" : "未开启" }}
          </span>
        </div>

        <div v-if="profile.id === 0" class="muted cell-meta">
          当前使用静态 <span class="mono" translate="no">ADMIN_TOKEN</span> 兼容会话登录，不支持配置 OTP。请使用真实管理员账号登录操作。
        </div>

        <template v-else-if="!profile.totp_enabled">
          <div v-if="!totpSetupData">
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

          <div v-else class="sub-box" style="gap: 14px">
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
                2. 输入验证器上生成的 6 位数字口令以完成绑定
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
        </template>

        <template v-else>
          <form class="sub-box" @submit.prevent="confirmDisableTotp">
            <strong style="font-size: 14px; color: var(--fg)">关闭 / 解绑 OTP 二步验证</strong>
            <p class="panel-sub">输入当前 6 位 OTP 口令或您的账号登录密码即可解除绑定。</p>
            <div class="form-grid-2">
              <label class="field-label">
                6 位动态口令（优先）
                <input
                  v-model="disableCode"
                  name="disable_otp_code"
                  maxlength="8"
                  inputmode="numeric"
                  autocomplete="one-time-code"
                  spellcheck="false"
                  placeholder="例如 123456"
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
    </template>
  </div>
</template>
