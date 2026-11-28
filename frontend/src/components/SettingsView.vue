<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { Copy, RefreshCw, Save } from "lucide-vue-next";
import { apiClient } from "../api/client";
import {
  ensureWriteAuthorized,
  isAdminUser,
  notify,
  refreshBootstrapState,
  setLanguageMode,
} from "../composables/useAppStore";
import ShipsView from "./ShipsView.vue";
import UsersManagePanel from "./UsersManagePanel.vue";
import type { LanguageMode, UserRole } from "../types/api";
import { STORE_FILTER_META, STORE_SORT_OPTIONS } from "../utils/localization";

export type SettingsSubTab = "general" | "ships" | "sso" | "users";

const props = defineProps<{
  initialSubTab?: SettingsSubTab;
  subTabRequestKey?: number;
  openCreateUserSignal?: number;
}>();

const emit = defineEmits<{
  (e: "consumeCreateUserSignal"): void;
}>();

const activeSubTab = ref<SettingsSubTab>(props.initialSubTab || "general");
const loading = ref(false);
const saving = ref(false);
const syncingFull = ref(false);
const apiKeyConfigured = ref(false);
const authRequireLoginEnv = ref(true);

const callbackUrl = computed(() => `${window.location.origin}/auth/callback`);

const form = reactive({
  scrape_interval_seconds: 300,
  catalog_sync_interval_seconds: 86400,
  default_language: "bilingual" as LanguageMode,
  default_store_filters: [] as string[],
  default_store_sort: "price-desc",
  log_retention_days: 30,
  upgrade_planner_enabled: false,
  translate_enabled: false,
  translate_api_base: "https://api.openai.com/v1",
  translate_model: "gpt-4o-mini",
  sso_enabled: false,
  sso_provider_name: "OIDC",
  sso_issuer_url: "",
  sso_client_id: "",
  sso_scopes: "openid profile email",
  sso_auto_register: true,
  sso_default_role: "user" as UserRole,
});

const SUBTABS = computed<{ id: SettingsSubTab; label: string }[]>(() => {
  const tabs: { id: SettingsSubTab; label: string }[] = [
    { id: "general", label: "全局设置" },
    { id: "ships", label: "中文译源" },
    { id: "sso", label: "OIDC" },
  ];
  if (isAdminUser()) {
    tabs.push({ id: "users", label: "用户管理" });
  }
  return tabs;
});

function toggleDefaultFilterKey(key: string): void {
  const idx = form.default_store_filters.indexOf(key);
  if (idx === -1) {
    form.default_store_filters.push(key);
  } else {
    form.default_store_filters.splice(idx, 1);
  }
}

watch(
  [() => props.initialSubTab, () => props.subTabRequestKey],
  ([subTab]) => {
    if (subTab) {
      activeSubTab.value = subTab;
    }
  }
);

async function loadSettings(): Promise<void> {
  loading.value = true;
  try {
    const cfg = await apiClient.getSettings();
    form.scrape_interval_seconds = cfg.scrape_interval_seconds;
    form.catalog_sync_interval_seconds = cfg.catalog_sync_interval_seconds;
    form.default_language = cfg.default_language;
    form.default_store_filters = [...(cfg.default_store_filters || [])];
    form.default_store_sort = cfg.default_store_sort || "price-desc";
    form.log_retention_days = cfg.log_retention_days;
    form.upgrade_planner_enabled = cfg.upgrade_planner_enabled;
    form.translate_enabled = cfg.translate_enabled;
    form.translate_api_base = cfg.translate_api_base;
    form.translate_model = cfg.translate_model;
    apiKeyConfigured.value = cfg.translate_api_key_configured;
    authRequireLoginEnv.value = cfg.auth_require_login;
    form.sso_enabled = cfg.sso_enabled;
    form.sso_provider_name = cfg.sso_provider_name;
    form.sso_issuer_url = cfg.sso_issuer_url;
    form.sso_client_id = cfg.sso_client_id;
    form.sso_scopes = cfg.sso_scopes;
    form.sso_auto_register = cfg.sso_auto_register;
    form.sso_default_role = cfg.sso_default_role;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载系统配置失败", "error");
  } finally {
    loading.value = false;
  }
}

async function saveSettings(): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  saving.value = true;
  try {
    const updated = await apiClient.updateSettings({
      scrape_interval_seconds: form.scrape_interval_seconds,
      catalog_sync_interval_seconds: form.catalog_sync_interval_seconds,
      default_language: form.default_language,
      default_store_filters: [...form.default_store_filters],
      default_store_sort: form.default_store_sort,
      log_retention_days: form.log_retention_days,
      upgrade_planner_enabled: form.upgrade_planner_enabled,
      translate_enabled: form.translate_enabled,
      translate_api_base: form.translate_api_base,
      translate_model: form.translate_model,
      sso_enabled: form.sso_enabled,
      sso_provider_name: form.sso_provider_name,
      sso_issuer_url: form.sso_issuer_url,
      sso_client_id: form.sso_client_id,
      sso_scopes: form.sso_scopes,
      sso_auto_register: form.sso_auto_register,
      sso_default_role: form.sso_default_role,
    });
    setLanguageMode(updated.default_language);
    await refreshBootstrapState();
    notify("系统动态配置已热更新并立即生效", "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存系统配置失败", "error");
  } finally {
    saving.value = false;
  }
}

async function triggerFullEnrichmentSync(): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  syncingFull.value = true;
  try {
    const res = await apiClient.refreshCcus({ syncCatalog: true, enrich: true });
    await refreshBootstrapState();
    notify(`全量目录、游戏内数据及 CCU 同步完成（在售 ${res.seen_skus} 条 CCU）`, "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "全量富化同步失败", "error");
  } finally {
    syncingFull.value = false;
  }
}

async function copyCallbackUrl(): Promise<void> {
  try {
    await navigator.clipboard.writeText(callbackUrl.value);
    notify("已复制 OIDC 回调 URL 到剪贴板", "success");
  } catch {
    notify("复制失败，请手动选中文本框中的地址复制", "info");
  }
}

onMounted(() => {
  void loadSettings();
});
</script>

<template>
  <section class="settings-section">
    <!-- 子导航标签 -->
    <div class="subtabs" role="tablist" aria-label="系统设置分类">
      <button
        v-for="tab in SUBTABS"
        :key="tab.id"
        type="button"
        role="tab"
        class="chip"
        :aria-selected="activeSubTab === tab.id"
        @click="activeSubTab = tab.id"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 子页面内容区（支持标签切换动效） -->
    <Transition name="holo-panel" mode="out-in">
      <!-- 1. 全局设置 -->
      <form
        v-if="activeSubTab === 'general'"
        key="general"
        class="panel form-stack"
        @submit.prevent="saveSettings"
      >
        <div class="panel-header">
          <div>
            <h2 class="panel-title">全局设置</h2>
            <p class="panel-sub">
              保存后写入数据库 <span translate="no">system_settings</span> 表并立即热生效，无需重启容器。
            </p>
          </div>
        <button
          type="button"
          class="navbtn"
          :disabled="syncingFull"
          @click="triggerFullEnrichmentSync"
        >
          <RefreshCw :size="14" :class="{ 'spin-icon': syncingFull }" aria-hidden="true" />
          {{ syncingFull ? "正在全量拉取…" : "立即全量富化（Wiki/UEX/货舱）" }}
        </button>
      </div>

      <div v-if="loading" class="muted panel-loading">
        加载配置中…
      </div>

      <template v-else>
        <div class="form-grid-2">
          <label class="field-label">
            商店与 CCU 抓取间隔（秒，最小 30）
            <input
              v-model.number="form.scrape_interval_seconds"
              name="scrape_interval_seconds"
              type="number"
              min="30"
              max="86400"
              required
              class="input"
            />
          </label>

          <label class="field-label">
            舰船目录与外部富化/词典同步间隔（秒）
            <input
              v-model.number="form.catalog_sync_interval_seconds"
              name="catalog_sync_interval_seconds"
              type="number"
              min="300"
              max="604800"
              required
              class="input"
            />
          </label>

          <label class="field-label">
            默认船名显示模式
            <select v-model="form.default_language" name="default_language" class="select">
              <option value="bilingual">中英双语（默认）</option>
              <option value="zh">仅显示中文</option>
              <option value="en">仅显示英文</option>
            </select>
          </label>

          <label class="field-label">
            历史日志自动保留天数（0 表示永久保留）
            <input
              v-model.number="form.log_retention_days"
              name="log_retention_days"
              type="number"
              min="0"
              max="3650"
              required
              class="input"
            />
          </label>
        </div>

        <div class="sub-box stack-md">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">首页（在售舰船）全站默认筛选条件与排序</strong>
              <p class="panel-sub">
                当用户尚未自定义个人默认条件或匿名访问时生效。例如勾选「仅 WBCCU」后，打开首页即自动仅展示 WBCCU。
              </p>
            </div>
            <button
              v-if="form.default_store_filters.length"
              type="button"
              class="link"
              @click="form.default_store_filters = []"
            >
              清空默认筛选
            </button>
          </div>

          <div class="filter-check-grid" role="group" aria-label="首页默认筛选条件">
            <button
              v-for="item in STORE_FILTER_META"
              :key="item.key"
              type="button"
              class="chip"
              :aria-pressed="form.default_store_filters.includes(item.key)"
              @click="toggleDefaultFilterKey(item.key)"
            >
              {{ item.label }}
            </button>
          </div>

          <label class="field-label" style="max-width: 320px">
            首页默认排序方式
            <select v-model="form.default_store_sort" name="default_store_sort" class="select">
              <option v-for="opt in STORE_SORT_OPTIONS" :key="opt.key" :value="opt.key">
                {{ opt.label }}
              </option>
            </select>
          </label>
        </div>

        <div class="sub-box">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">升级路线规划功能（<span translate="no">/plan</span>）</strong>
              <p class="panel-sub">
                默认关闭。启用后顶部导航与商店卡片显示「升级路线规划」入口，并在后台自动映射多跳 CCU 路径（远程中文译源 URL 已移至「中文译源」标签页维护）。
              </p>
            </div>
            <label class="inline-check">
              <input v-model="form.upgrade_planner_enabled" type="checkbox" />
              启用升级路线规划
            </label>
          </div>
        </div>

        <div class="sub-box">
          <div class="row-between">
            <div>
              <strong style="font-size: 14px">新船自动翻译兜底（OpenAI 兼容接口）</strong>
              <p class="panel-sub">
                环境变量 <span class="mono" translate="no">TRANSLATE_API_KEY</span>：
                <span class="pill pill-inline" :class="apiKeyConfigured ? 'good' : 'neutral'">
                  {{ apiKeyConfigured ? "已配置" : "未配置" }}
                </span>
              </p>
            </div>
            <label class="inline-check">
              <input v-model="form.translate_enabled" type="checkbox" />
              启用兜底翻译
            </label>
          </div>

          <div class="form-grid-2">
            <label class="field-label">
              接口 Base URL
              <input
                v-model="form.translate_api_base"
                name="translate_api_base"
                type="url"
                autocomplete="off"
                spellcheck="false"
                class="input"
              />
            </label>
            <label class="field-label">
              模型名称
              <input
                v-model="form.translate_model"
                name="translate_model"
                type="text"
                autocomplete="off"
                spellcheck="false"
                class="input"
              />
            </label>
          </div>
        </div>

        <div class="row-end">
          <button type="submit" class="navbtn primary" :disabled="saving">
            <Save :size="14" aria-hidden="true" />
            {{ saving ? "保存中…" : "保存配置" }}
          </button>
        </div>
      </template>
    </form>

      <!-- 2. 中文译源（已从顶部主导航移入系统设置） -->
      <ShipsView v-else-if="activeSubTab === 'ships'" key="ships" />

      <!-- 3. OIDC 单点登录与全站访问控制 -->
      <form
        v-else-if="activeSubTab === 'sso'"
        key="sso"
        class="panel form-stack"
        @submit.prevent="saveSettings"
      >
      <div>
        <h2 class="panel-title">站点访问控制与 OIDC 单点登录（PKCE 无 Secret 模式）</h2>
        <p class="panel-sub">
          配置基于 Authorization Code + PKCE (S256) 的公共客户端 OIDC 单点登录（无需 Client Secret），并查看全站强制登录环境变量状态。
        </p>
      </div>

      <div class="sub-box">
        <div class="row-between">
          <div>
            <strong style="font-size: 14px">全站强制登录访问（仅限环境变量控制）</strong>
            <p class="panel-sub">
              环境变量 <span class="mono" translate="no">AUTH_REQUIRE_LOGIN</span>：
              <span class="pill pill-inline" :class="authRequireLoginEnv ? 'good' : 'neutral'">
                {{ authRequireLoginEnv ? "已开启" : "已关闭" }}
              </span>
              · 为保障访问控制边界，该项仅支持通过服务端 <span class="mono" translate="no">.env</span> 或容器环境变量配置并重启生效，Web UI 不可修改。
            </p>
          </div>
        </div>
      </div>

      <div class="sub-box stack-md">
        <div class="row-between">
          <strong style="font-size: 14px" translate="no">OIDC PKCE Single Sign-On</strong>
          <label class="inline-check">
            <input v-model="form.sso_enabled" type="checkbox" />
            启用 OIDC 登录
          </label>
        </div>

        <div class="form-grid-2">
          <label class="field-label">
            OIDC 登录按钮名称
            <input
              v-model="form.sso_provider_name"
              name="sso_provider_name"
              autocomplete="off"
              spellcheck="false"
              placeholder="ZITADEL"
              class="input"
            />
          </label>
          <label class="field-label">
            OIDC Issuer 根地址
            <input
              v-model="form.sso_issuer_url"
              name="sso_issuer_url"
              type="url"
              autocomplete="off"
              spellcheck="false"
              placeholder="例如：https://auth.example.com"
              class="input"
            />
          </label>
          <label class="field-label">
            Client ID（无需 Client Secret，请在 ZITADEL 选择 User Agent / Native + PKCE）
            <input
              v-model="form.sso_client_id"
              name="sso_client_id"
              autocomplete="off"
              spellcheck="false"
              placeholder="例如：28193746123984@ccujailer"
              class="input mono"
            />
          </label>
          <label class="field-label">
            OIDC Scopes
            <input
              v-model="form.sso_scopes"
              name="sso_scopes"
              autocomplete="off"
              spellcheck="false"
              placeholder="openid profile email"
              class="input"
            />
          </label>
          <label class="field-label">
            首次通过 SSO 登录时自动创建账号
            <select v-model="form.sso_auto_register" name="sso_auto_register" class="select">
              <option :value="true">开启（首次 SSO 登录自动入库）</option>
              <option :value="false">关闭（仅允许已预设或已绑定用户登录）</option>
            </select>
          </label>
          <label class="field-label">
            SSO 自动注册账号默认角色
            <select v-model="form.sso_default_role" name="sso_default_role" class="select">
              <option value="user">普通用户 (user)</option>
              <option value="admin">管理员 (admin)</option>
            </select>
          </label>
        </div>

        <div class="field-label">
          <span>在 ZITADEL / OIDC 应用后台填写入白名单的 Redirect URI（回调地址）：</span>
          <div class="copy-row">
            <input
              :value="callbackUrl"
              readonly
              aria-label="OIDC Redirect URI 回调地址"
              class="input mono"
            />
            <button type="button" class="navbtn" @click="copyCallbackUrl">
              <Copy :size="14" aria-hidden="true" />
              复制
            </button>
          </div>
        </div>
      </div>

      <div class="row-end">
        <button type="submit" class="navbtn primary" :disabled="saving">
          <Save :size="14" aria-hidden="true" />
          {{ saving ? "保存中…" : "保存 OIDC 配置" }}
        </button>
      </div>
    </form>

      <!-- 4. 用户管理（严格仅管理员可见与访问） -->
      <UsersManagePanel
        v-else-if="activeSubTab === 'users' && isAdminUser()"
        key="users"
        :auto-open-create-signal="openCreateUserSignal"
        @consume-create-signal="emit('consumeCreateUserSignal')"
      />
    </Transition>
  </section>
</template>
