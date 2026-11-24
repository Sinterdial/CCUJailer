<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { KeyRound, ShieldCheck, UserPlus } from "lucide-vue-next";
import { apiClient } from "../api/client";
import { applySessionLogin, appStore, notify } from "../composables/useAppStore";

const submitting = ref(false);
const ssoRedirecting = ref(false);
const mfaToken = ref("");
const callbackProcessing = ref(false);
const callbackError = ref("");
const formError = ref("");
const authMode = ref<"login" | "register">("login");

const setupForm = reactive({
  username: "",
  displayName: "",
  password: "",
  confirmPassword: "",
});

const loginForm = reactive({
  username: "",
  password: "",
  otpCode: "",
});

const registerForm = reactive({
  username: "",
  displayName: "",
  email: "",
  password: "",
  confirmPassword: "",
  inviteCode: "",
});

const needsSetup = computed(() => appStore.authLoaded && !appStore.authStatus.initialized);
const isMfaStep = computed(() => Boolean(mfaToken.value));
const registrationMode = computed(() => appStore.authStatus.registration_mode || "closed");
const canSelfRegister = computed(
  () => !needsSetup.value && registrationMode.value !== "closed"
);
const ssoButtonLabel = computed(
  () => `使用 ${appStore.authStatus.sso_provider_name || "ZITADEL"} SSO 登录`
);

watch(canSelfRegister, (allowed) => {
  if (!allowed) {
    authMode.value = "login";
  }
});

function switchAuthMode(nextMode: "login" | "register"): void {
  authMode.value = nextMode;
  formError.value = "";
}

function getCallbackUrl(): string {
  return `${window.location.origin}/auth/callback`;
}

async function submitSetup(): Promise<void> {
  formError.value = "";
  if (setupForm.password !== setupForm.confirmPassword) {
    formError.value = "两次输入的密码不一致";
    notify("两次输入的密码不一致", "error");
    return;
  }
  submitting.value = true;
  try {
    const res = await apiClient.setupInitialAdmin({
      username: setupForm.username.trim(),
      password: setupForm.password,
      display_name: setupForm.displayName.trim(),
    });
    await applySessionLogin(res.access_token);
    notify("管理员账号创建成功，欢迎使用 CCUJailer", "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "初始化管理员失败";
    formError.value = msg;
    notify(msg, "error");
  } finally {
    submitting.value = false;
  }
}

async function submitLogin(): Promise<void> {
  formError.value = "";
  submitting.value = true;
  try {
    const res = await apiClient.login({
      username: loginForm.username.trim(),
      password: loginForm.password,
    });
    if (res.mfa_required && res.mfa_token) {
      mfaToken.value = res.mfa_token;
      loginForm.otpCode = "";
      notify("请输入验证器 6 位 OTP 动态口令", "info");
      return;
    }
    await applySessionLogin(res.access_token);
    notify("登录成功", "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "登录失败";
    formError.value = msg;
    notify(msg, "error");
  } finally {
    submitting.value = false;
  }
}

async function submitRegister(): Promise<void> {
  formError.value = "";
  if (registerForm.password !== registerForm.confirmPassword) {
    formError.value = "两次输入的密码不一致";
    notify("两次输入的密码不一致", "error");
    return;
  }
  if (registrationMode.value === "invite_only" && !registerForm.inviteCode.trim()) {
    formError.value = "当前仅限邀请制注册，请输入有效邀请码";
    notify(formError.value, "error");
    return;
  }
  submitting.value = true;
  try {
    const res = await apiClient.register({
      username: registerForm.username.trim(),
      password: registerForm.password,
      display_name: registerForm.displayName.trim(),
      email: registerForm.email.trim(),
      ...(registrationMode.value === "invite_only"
        ? { invite_code: registerForm.inviteCode.trim() }
        : {}),
    });
    if (window.location.search.includes("invite=")) {
      window.history.replaceState(null, "", window.location.pathname || "/");
    }
    await applySessionLogin(res.access_token);
    notify("注册成功，欢迎加入 CCUJailer", "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "注册失败";
    formError.value = msg;
    notify(msg, "error");
  } finally {
    submitting.value = false;
  }
}

async function submitOtp(): Promise<void> {
  formError.value = "";
  submitting.value = true;
  try {
    const res = await apiClient.loginOtp({
      mfa_token: mfaToken.value,
      otp_code: loginForm.otpCode.trim(),
    });
    mfaToken.value = "";
    await applySessionLogin(res.access_token);
    notify("二步验证通过，欢迎回来", "success");
  } catch (err) {
    const msg = err instanceof Error ? err.message : "动态口令验证失败";
    formError.value = msg;
    notify(msg, "error");
  } finally {
    submitting.value = false;
  }
}

async function startSsoLogin(): Promise<void> {
  ssoRedirecting.value = true;
  try {
    const res = await apiClient.getSsoAuthorizeUrl(getCallbackUrl());
    window.location.assign(res.authorize_url);
  } catch (err) {
    notify(err instanceof Error ? err.message : "无法发起 SSO 登录", "error");
    ssoRedirecting.value = false;
  }
}

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const state = params.get("state");
  const oauthErr = params.get("error_description") || params.get("error");
  const inviteParam = params.get("invite");

  if (window.location.pathname === "/auth/callback" || (code && state)) {
    if (oauthErr) {
      callbackError.value = `SSO 授权失败：${oauthErr}`;
      window.history.replaceState(null, "", "/");
      return;
    }
    if (code && state) {
      callbackProcessing.value = true;
      try {
        const res = await apiClient.completeSsoCallback({
          code,
          state,
          redirect_uri: getCallbackUrl(),
        });
        window.history.replaceState(null, "", "/");
        await applySessionLogin(res.access_token);
        notify("SSO 登录成功", "success");
      } catch (err) {
        callbackError.value = err instanceof Error ? err.message : "SSO 回调校验失败";
        window.history.replaceState(null, "", "/");
      } finally {
        callbackProcessing.value = false;
      }
    }
    return;
  }

  if (inviteParam && canSelfRegister.value) {
    registerForm.inviteCode = inviteParam.trim();
    authMode.value = "register";
  }
});
</script>

<template>
  <div class="auth-stage">
    <div v-if="callbackProcessing" class="auth-card text-center">
      <div class="auth-brand centered">
        <img src="/favicon.svg" alt="" aria-hidden="true" class="brand-logo" />
        <span translate="no">CCUJailer</span>
        <span class="version-badge" translate="no">v{{ appStore.authStatus.app_version }}</span>
      </div>
      <p class="muted" style="margin: 0">正在校验 OIDC PKCE 授权凭证，请稍候…</p>
    </div>

    <div v-else-if="needsSetup" class="auth-card">
      <div class="auth-brand">
        <img src="/favicon.svg" alt="" aria-hidden="true" class="brand-logo" />
        <span translate="no">CCUJailer</span>
        <span class="version-badge" translate="no">v{{ appStore.authStatus.app_version }}</span>
      </div>
      <div>
        <h2 class="auth-title">创建首个管理员账号</h2>
        <p class="panel-sub">
          检测到当前尚未配置任何用户，请先创建系统管理员以开启商店监控、Webhook 规则与 SSO 设置。
        </p>
      </div>

      <div v-if="formError" class="auth-error" role="alert">
        {{ formError }}
      </div>

      <form class="form-stack" @submit.prevent="submitSetup">
        <label class="field-label">
          管理员用户名
          <input
            v-model="setupForm.username"
            name="username"
            required
            minlength="2"
            autocomplete="username"
            spellcheck="false"
            placeholder="例如 admin"
            class="input"
          />
        </label>
        <label class="field-label">
          显示昵称（可选）
          <input
            v-model="setupForm.displayName"
            name="display_name"
            autocomplete="nickname"
            placeholder="例如 系统管理员"
            class="input"
          />
        </label>
        <label class="field-label">
          登录密码（至少 8 位）
          <input
            v-model="setupForm.password"
            name="password"
            type="password"
            required
            minlength="8"
            autocomplete="new-password"
            placeholder="至少 8 位密码"
            class="input"
          />
        </label>
        <label class="field-label">
          确认密码
          <input
            v-model="setupForm.confirmPassword"
            name="confirm_password"
            type="password"
            required
            minlength="8"
            autocomplete="new-password"
            placeholder="再次输入密码"
            class="input"
          />
        </label>
        <button type="submit" class="navbtn primary btn-lg" :disabled="submitting">
          <UserPlus :size="15" aria-hidden="true" />
          {{ submitting ? "创建中…" : "完成初始化并登录" }}
        </button>
      </form>
    </div>

    <div v-else class="auth-card">
      <div class="auth-brand">
        <img src="/favicon.svg" alt="" aria-hidden="true" class="brand-logo" />
        <span translate="no">CCUJailer</span>
        <span class="version-badge" translate="no">v{{ appStore.authStatus.app_version }}</span>
      </div>
      <div>
        <h2 class="auth-title">
          {{
            isMfaStep
              ? "OTP 二步验证"
              : authMode === "register"
                ? "注册 CCUJailer 账号"
                : "登录 CCUJailer"
          }}
        </h2>
        <p class="panel-sub">
          {{
            isMfaStep
              ? "请输入身份验证器 App 中显示的 6 位动态口令"
              : authMode === "register"
                ? registrationMode === "invite_only"
                  ? "当前为邀请制注册，请填写有效邀请码完成账号创建"
                  : "创建你的个人账号以浏览商店、追踪 CCU 与配置二步验证"
                : "登录后可查看实时 RSI 商店价格、CCU 差价与升级路线规划"
          }}
        </p>
      </div>

      <div v-if="!isMfaStep && canSelfRegister" class="auth-mode-tabs" role="group" aria-label="登录与注册切换">
        <button
          type="button"
          class="auth-mode-tab"
          :aria-pressed="authMode === 'login'"
          @click="switchAuthMode('login')"
        >
          账号登录
        </button>
        <button
          type="button"
          class="auth-mode-tab"
          :aria-pressed="authMode === 'register'"
          @click="switchAuthMode('register')"
        >
          注册新账号
        </button>
      </div>

      <div v-if="callbackError || formError" class="auth-error" role="alert">
        {{ callbackError || formError }}
      </div>

      <form v-if="isMfaStep" class="form-stack" @submit.prevent="submitOtp">
        <label class="field-label">
          6 位 OTP 动态口令
          <input
            v-model="loginForm.otpCode"
            name="otp_code"
            type="text"
            inputmode="numeric"
            autocomplete="one-time-code"
            spellcheck="false"
            required
            maxlength="8"
            placeholder="000000"
            class="input mono otp-input"
          />
        </label>
        <div class="auth-actions">
          <button
            type="button"
            class="navbtn flex-1"
            @click="mfaToken = ''; formError = ''"
          >
            返回
          </button>
          <button type="submit" class="navbtn primary flex-2" :disabled="submitting">
            <ShieldCheck :size="15" aria-hidden="true" />
            {{ submitting ? "验证中…" : "确认登录" }}
          </button>
        </div>
      </form>

      <form v-else-if="canSelfRegister && authMode === 'register'" class="form-stack" @submit.prevent="submitRegister">
        <label v-if="registrationMode === 'invite_only'" class="field-label">
          邀请码（必填）
          <input
            v-model="registerForm.inviteCode"
            name="reg_invite_code"
            required
            autocomplete="off"
            spellcheck="false"
            placeholder="请输入管理员发放的邀请码"
            class="input mono"
          />
        </label>
        <label class="field-label">
          用户名（2–32 位字母、数字或下划线）
          <input
            v-model="registerForm.username"
            name="reg_username"
            required
            minlength="2"
            autocomplete="username"
            spellcheck="false"
            placeholder="例如 pilot_user"
            class="input"
          />
        </label>
        <div class="form-grid-2">
          <label class="field-label">
            显示昵称（可选）
            <input
              v-model="registerForm.displayName"
              name="reg_display_name"
              autocomplete="nickname"
              placeholder="例如 领航员"
              class="input"
            />
          </label>
          <label class="field-label">
            电子邮箱（可选）
            <input
              v-model="registerForm.email"
              name="reg_email"
              type="email"
              autocomplete="email"
              spellcheck="false"
              placeholder="name@example.com"
              class="input"
            />
          </label>
        </div>
        <label class="field-label">
          登录密码（至少 8 位）
          <input
            v-model="registerForm.password"
            name="reg_password"
            type="password"
            required
            minlength="8"
            autocomplete="new-password"
            placeholder="至少 8 位密码"
            class="input"
          />
        </label>
        <label class="field-label">
          确认密码
          <input
            v-model="registerForm.confirmPassword"
            name="reg_confirm_password"
            type="password"
            required
            minlength="8"
            autocomplete="new-password"
            placeholder="再次输入密码"
            class="input"
          />
        </label>
        <button type="submit" class="navbtn primary btn-lg" :disabled="submitting">
          <UserPlus :size="15" aria-hidden="true" />
          {{ submitting ? "注册中…" : "完成注册并登录" }}
        </button>
      </form>

      <template v-else>
        <form class="form-stack" @submit.prevent="submitLogin">
          <label class="field-label">
            用户名
            <input
              v-model="loginForm.username"
              name="username"
              required
              autocomplete="username"
              spellcheck="false"
              placeholder="请输入用户名"
              class="input"
            />
          </label>
          <label class="field-label">
            密码
            <input
              v-model="loginForm.password"
              name="password"
              type="password"
              required
              autocomplete="current-password"
              placeholder="请输入密码"
              class="input"
            />
          </label>
          <button type="submit" class="navbtn primary btn-lg" :disabled="submitting">
            <KeyRound :size="15" aria-hidden="true" />
            {{ submitting ? "登录中…" : "账号密码登录" }}
          </button>
        </form>

        <template v-if="appStore.authStatus.sso_enabled">
          <div class="divider">或</div>
          <button
            type="button"
            class="navbtn btn-full"
            :disabled="ssoRedirecting"
            @click="startSsoLogin"
          >
            <ShieldCheck :size="15" aria-hidden="true" />
            {{ ssoRedirecting ? "正在跳转认证中心…" : ssoButtonLabel }}
          </button>
        </template>
      </template>
    </div>
  </div>
</template>
