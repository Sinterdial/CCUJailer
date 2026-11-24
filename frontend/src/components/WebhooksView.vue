<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Edit3,
  GripVertical,
  Plus,
  Send,
  Trash2,
  X,
} from "lucide-vue-next";
import { apiClient } from "../api/client";
import { ensureWriteAuthorized, formatUtcTime, notify } from "../composables/useAppStore";
import type {
  WebhookLogItem,
  WebhookMethod,
  WebhookProviderType,
  WebhookRule,
  WebhookRuleCreatePayload,
} from "../types/api";

interface HeaderPair {
  key: string;
  value: string;
}

const PROVIDER_LABELS: Record<WebhookProviderType, string> = {
  generic: "标准通用 JSON",
  feishu: "飞书交互卡片",
  wecom: "企业微信 Markdown",
  discord: "Discord Embed",
  custom: "自定义 JSON 模板",
};

const WEBHOOK_EVENT_OPTIONS: { value: string; label: string; desc: string }[] = [
  {
    value: "ccu.warbond_upgrade",
    label: "Warbond 升级",
    desc: "仅推送 WBCCU 上架、返场或降价通知",
  },
  {
    value: "store.warbond_ship",
    label: "Warbond 整船",
    desc: "仅推送单船商店 Warbond 版本上架、返场或降价",
  },
  {
    value: "store.special_sale",
    label: "特殊销售",
    desc: "Best in Show 等特定纪念/限量发售版本",
  },
  {
    value: "ccu.new_listing",
    label: "全部 CCU 上架",
    desc: "任意升级包（含标准版与 WBCCU）上架/返场",
  },
  {
    value: "ccu.price_changed",
    label: "全部 CCU 降价",
    desc: "任意升级包（含标准版与 WBCCU）发生降价变动时推送",
  },
];

const DEFAULT_RULE_EVENTS = [
  "ccu.warbond_upgrade",
  "store.warbond_ship",
  "store.special_sale",
];

function eventBadgeLabel(ev: string): string {
  return WEBHOOK_EVENT_OPTIONS.find((item) => item.value === ev)?.label || ev;
}

const rules = ref<WebhookRule[]>([]);
const logs = ref<WebhookLogItem[]>([]);
const loading = ref(false);
const modalOpen = ref(false);
const saving = ref(false);
const testingId = ref<string | null>(null);
const editingId = ref<string | null>(null);

const form = reactive({
  name: "",
  enabled: true,
  url: "",
  method: "POST" as WebhookMethod,
  provider_type: "generic" as WebhookProviderType,
  custom_template: "",
  events: [...DEFAULT_RULE_EVENTS] as string[],
  headers: [] as HeaderPair[],
  keywordsText: "",
  min_original_price: "",
  max_original_price: "",
  min_wb_price: "",
  max_wb_price: "",
  min_savings: "",
});

async function loadAll(): Promise<void> {
  loading.value = true;
  try {
    const [ruleResp, logResp] = await Promise.all([
      apiClient.listWebhooks(),
      apiClient.listWebhookLogs({ limit: 40 }),
    ]);
    rules.value = ruleResp.items;
    logs.value = logResp.items;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载 Webhook 配置失败", "error");
  } finally {
    loading.value = false;
  }
}

function openCreateModal(): void {
  if (!ensureWriteAuthorized()) {
    return;
  }
  editingId.value = null;
  form.name = "";
  form.enabled = true;
  form.url = "";
  form.method = "POST";
  form.provider_type = "generic";
  form.custom_template =
    '{"ship":"{{ship_name_zh}} ({{ship_name_en}})","type":"{{sale_type}}","price":{{discounted_price}},"warbond":{{is_warbond}},"image":"{{image_url}}"}';
  form.events = [...DEFAULT_RULE_EVENTS];
  form.headers = [];
  form.keywordsText = "";
  form.min_original_price = "";
  form.max_original_price = "";
  form.min_wb_price = "";
  form.max_wb_price = "";
  form.min_savings = "";
  modalOpen.value = true;
}

function openEditModal(rule: WebhookRule): void {
  if (!ensureWriteAuthorized()) {
    return;
  }
  editingId.value = rule.id;
  form.name = rule.name;
  form.enabled = rule.enabled;
  form.url = rule.url;
  form.method = rule.method;
  form.provider_type = rule.provider_type;
  form.custom_template = rule.custom_template || "";
  const rawEvents = [...(rule.events || [])];
  if (
    rule.filters?.only_warbond &&
    !rawEvents.includes("ccu.warbond_upgrade") &&
    !rawEvents.includes("store.warbond_ship")
  ) {
    form.events = Array.from(
      new Set([
        ...rawEvents.filter((ev) => ev !== "ccu.new_listing" && ev !== "ccu.price_changed"),
        "ccu.warbond_upgrade",
      ])
    );
  } else {
    form.events = rawEvents;
  }
  form.headers = Object.entries(rule.headers || {}).map(([key, value]) => ({ key, value }));
  form.keywordsText = (rule.filters?.ship_keywords || []).join(", ");
  form.min_original_price =
    rule.filters?.min_original_price != null ? String(rule.filters.min_original_price) : "";
  form.max_original_price =
    rule.filters?.max_original_price != null ? String(rule.filters.max_original_price) : "";
  form.min_wb_price = rule.filters?.min_wb_price != null ? String(rule.filters.min_wb_price) : "";
  form.max_wb_price = rule.filters?.max_wb_price != null ? String(rule.filters.max_wb_price) : "";
  form.min_savings = rule.filters?.min_savings != null ? String(rule.filters.min_savings) : "";
  modalOpen.value = true;
}

function addHeaderRow(): void {
  form.headers.push({ key: "", value: "" });
}

function removeHeaderRow(index: number): void {
  form.headers.splice(index, 1);
}

function toNullableNumber(val: string): number | null {
  const cleaned = val.trim();
  if (!cleaned) {
    return null;
  }
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : null;
}

async function saveRule(): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  if (form.events.length === 0) {
    notify("请至少选择一种通知事件类型", "error");
    return;
  }
  saving.value = true;
  try {
    const headerMap: Record<string, string> = {};
    for (const pair of form.headers) {
      if (pair.key.trim()) {
        headerMap[pair.key.trim()] = pair.value;
      }
    }
    const keywords = form.keywordsText
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

    const payload: WebhookRuleCreatePayload = {
      name: form.name.trim(),
      enabled: form.enabled,
      url: form.url.trim(),
      method: form.method,
      provider_type: form.provider_type,
      custom_template: form.provider_type === "custom" ? form.custom_template : null,
      headers: headerMap,
      events: [...form.events],
      filters: {
        only_warbond: false,
        ship_keywords: keywords,
        min_original_price: toNullableNumber(form.min_original_price),
        max_original_price: toNullableNumber(form.max_original_price),
        min_wb_price: toNullableNumber(form.min_wb_price),
        max_wb_price: toNullableNumber(form.max_wb_price),
        min_savings: toNullableNumber(form.min_savings),
      },
    };

    if (editingId.value) {
      await apiClient.updateWebhook(editingId.value, payload);
      notify("Webhook 规则已更新", "success");
    } else {
      await apiClient.createWebhook(payload);
      notify("Webhook 规则创建成功", "success");
    }
    modalOpen.value = false;
    await loadAll();
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存规则失败", "error");
  } finally {
    saving.value = false;
  }
}

async function toggleRuleEnabled(rule: WebhookRule): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  try {
    await apiClient.updateWebhook(rule.id, { enabled: !rule.enabled });
    await loadAll();
  } catch (err) {
    notify(err instanceof Error ? err.message : "切换状态失败", "error");
  }
}

async function removeRule(rule: WebhookRule): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  if (!window.confirm(`确定删除 Webhook 规则「${rule.name}」吗？其历史日志也会一并清除。`)) {
    return;
  }
  try {
    await apiClient.deleteWebhook(rule.id);
    notify("已删除 Webhook 规则", "success");
    await loadAll();
  } catch (err) {
    notify(err instanceof Error ? err.message : "删除规则失败", "error");
  }
}

async function testRule(rule: WebhookRule): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  testingId.value = rule.id;
  try {
    const res = await apiClient.testWebhook(rule.id);
    if (res.success) {
      notify(
        `测试通知投递成功 (HTTP ${res.status_code}, 耗时 ${res.duration_ms}ms)`,
        "success"
      );
    } else {
      notify(`测试通知失败：${res.error_message || res.status_code}`, "error");
    }
    await loadAll();
  } catch (err) {
    notify(err instanceof Error ? err.message : "测试请求执行失败", "error");
  } finally {
    testingId.value = null;
  }
}

const WEBHOOK_COLLAPSED_KEY = "ccujailer_webhook_collapsed_ids";
const WEBHOOK_ORDER_KEY = "ccujailer_webhook_custom_order";

function loadIdArray(storageKey: string): string[] {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function saveIdArray(storageKey: string, items: string[]): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(items));
  } catch {
    // 忽略本地存储写入失败
  }
}

const collapsedRuleIds = ref<Set<string>>(new Set(loadIdArray(WEBHOOK_COLLAPSED_KEY)));
const customRuleOrder = ref<string[]>(loadIdArray(WEBHOOK_ORDER_KEY));
const draggingRuleId = ref<string | null>(null);
const dragOverRuleId = ref<string | null>(null);

const orderedRules = computed(() => {
  if (customRuleOrder.value.length === 0) {
    return rules.value;
  }
  const indexMap = new Map(customRuleOrder.value.map((id, idx) => [id, idx]));
  return rules.value.slice().sort((a, b) => {
    const ia = indexMap.get(a.id);
    const ib = indexMap.get(b.id);
    if (ia !== undefined && ib !== undefined) {
      return ia - ib;
    }
    if (ia !== undefined) {
      return -1;
    }
    if (ib !== undefined) {
      return 1;
    }
    return 0;
  });
});

const allRulesCollapsed = computed(
  () => orderedRules.value.length > 0 && orderedRules.value.every((r) => collapsedRuleIds.value.has(r.id))
);

function toggleRuleCollapse(ruleId: string): void {
  const next = new Set(collapsedRuleIds.value);
  if (next.has(ruleId)) {
    next.delete(ruleId);
  } else {
    next.add(ruleId);
  }
  collapsedRuleIds.value = next;
  saveIdArray(WEBHOOK_COLLAPSED_KEY, [...next]);
}

function toggleAllRulesCollapse(): void {
  const next = new Set(collapsedRuleIds.value);
  if (allRulesCollapsed.value) {
    for (const r of orderedRules.value) {
      next.delete(r.id);
    }
  } else {
    for (const r of orderedRules.value) {
      next.add(r.id);
    }
  }
  collapsedRuleIds.value = next;
  saveIdArray(WEBHOOK_COLLAPSED_KEY, [...next]);
}

function applyRuleReorder(sourceId: string, targetIndex: number): void {
  const currentIds = orderedRules.value.map((r) => r.id);
  const fromIdx = currentIds.indexOf(sourceId);
  if (fromIdx === -1 || targetIndex < 0 || targetIndex >= currentIds.length || fromIdx === targetIndex) {
    return;
  }
  const reordered = currentIds.slice();
  const [moved] = reordered.splice(fromIdx, 1);
  reordered.splice(targetIndex, 0, moved);
  customRuleOrder.value = reordered;
  saveIdArray(WEBHOOK_ORDER_KEY, reordered);
}

function moveRuleByDirection(ruleId: string, direction: -1 | 1): void {
  const idx = orderedRules.value.findIndex((r) => r.id === ruleId);
  if (idx !== -1) {
    applyRuleReorder(ruleId, idx + direction);
  }
}

function handleRuleDragStart(ruleId: string, event: DragEvent): void {
  draggingRuleId.value = ruleId;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", ruleId);
  }
}

function handleRuleDragOver(ruleId: string): void {
  if (draggingRuleId.value && draggingRuleId.value !== ruleId) {
    dragOverRuleId.value = ruleId;
  }
}

function handleRuleDrop(targetId: string, event: DragEvent): void {
  const src = draggingRuleId.value || event.dataTransfer?.getData("text/plain") || "";
  const targetIdx = orderedRules.value.findIndex((r) => r.id === targetId);
  if (src && targetIdx !== -1) {
    applyRuleReorder(src, targetIdx);
  }
  draggingRuleId.value = null;
  dragOverRuleId.value = null;
}

function handleRuleDragEnd(): void {
  draggingRuleId.value = null;
  dragOverRuleId.value = null;
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && modalOpen.value) {
    modalOpen.value = false;
  }
}

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  void loadAll();
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <section class="stack-lg">
    <div class="panel">
      <div class="panel-header">
        <div>
          <h2 class="panel-title">Webhook 推送规则</h2>
          <p class="panel-sub">
            支持标准通用 JSON、飞书卡片、企业微信 Markdown、Discord Embed 与自定义模板（卡片支持折叠与拖曳排序）。
          </p>
        </div>
        <div class="inline-actions">
          <button
            v-if="orderedRules.length > 0"
            type="button"
            class="navbtn"
            @click="toggleAllRulesCollapse"
          >
            {{ allRulesCollapsed ? "全部展开" : "全部折叠" }}
          </button>
          <button type="button" class="navbtn primary" @click="openCreateModal">
            <Plus :size="14" aria-hidden="true" />
            新增规则
          </button>
        </div>
      </div>
    </div>

    <p v-if="!loading && orderedRules.length === 0" class="empty">
      尚未配置任何 Webhook 规则，点击右上角「新增规则」创建推送渠道。
    </p>

    <div v-else class="rules-grid">
      <article
        v-for="(rule, idx) in orderedRules"
        :key="rule.id"
        class="rule-card"
        :class="{
          collapsed: collapsedRuleIds.has(rule.id),
          'is-dragging': draggingRuleId === rule.id,
          'is-drag-over': dragOverRuleId === rule.id,
        }"
        draggable="true"
        @dragstart="handleRuleDragStart(rule.id, $event)"
        @dragover.prevent="handleRuleDragOver(rule.id)"
        @drop.prevent="handleRuleDrop(rule.id, $event)"
        @dragend="handleRuleDragEnd"
      >
        <div>
          <div class="rule-head">
            <div class="rule-title-group">
              <strong class="rule-title">{{ rule.name }}</strong>
              <span class="pill info">{{ PROVIDER_LABELS[rule.provider_type] || rule.provider_type }}</span>
              <span class="pill" :class="rule.enabled ? 'good' : 'neutral'">
                {{ rule.enabled ? "已启用" : "已暂停" }}
              </span>
            </div>
            <div class="card-controls">
              <span class="card-tool-btn drag-handle" title="按住拖曳调整顺序" aria-label="拖曳排序">
                <GripVertical :size="13" aria-hidden="true" />
              </span>
              <button
                type="button"
                class="card-tool-btn"
                :disabled="idx === 0"
                title="上移"
                aria-label="上移"
                @click="moveRuleByDirection(rule.id, -1)"
              >
                <ArrowUp :size="12" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="card-tool-btn"
                :disabled="idx === orderedRules.length - 1"
                title="下移"
                aria-label="下移"
                @click="moveRuleByDirection(rule.id, 1)"
              >
                <ArrowDown :size="12" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="card-tool-btn"
                :aria-expanded="!collapsedRuleIds.has(rule.id)"
                :title="collapsedRuleIds.has(rule.id) ? '展开卡片' : '折叠卡片'"
                @click="toggleRuleCollapse(rule.id)"
              >
                <ChevronDown v-if="collapsedRuleIds.has(rule.id)" :size="13" aria-hidden="true" />
                <ChevronUp v-else :size="13" aria-hidden="true" />
              </button>
              <button type="button" class="navbtn btn-sm" @click="toggleRuleEnabled(rule)">
                {{ rule.enabled ? "停用" : "启用" }}
              </button>
            </div>
          </div>
          <template v-if="!collapsedRuleIds.has(rule.id)">
            <p class="rule-url">{{ rule.method }} {{ rule.url }}</p>

            <div class="chips" style="margin-top: 10px">
              <span v-for="ev in rule.events || []" :key="ev" class="pill info">
                {{ eventBadgeLabel(ev) }}
              </span>
              <span
                v-if="
                  rule.filters?.only_warbond &&
                  !rule.events?.some((e) => e === 'ccu.warbond_upgrade' || e === 'store.warbond_ship')
                "
                class="pill warn"
              >
                仅 Warbond
              </span>
              <span v-for="kw in rule.filters?.ship_keywords || []" :key="kw" class="pill neutral">
                {{ kw }}
              </span>
              <span v-if="rule.filters?.min_savings != null" class="pill good">
                立省 >= ${{ rule.filters.min_savings }}
              </span>
              <span v-if="rule.filters?.max_wb_price != null" class="pill neutral">
                现价 <= ${{ rule.filters.max_wb_price }}
              </span>
            </div>
          </template>
        </div>

        <div v-if="!collapsedRuleIds.has(rule.id)" class="rule-actions">
          <button
            type="button"
            class="navbtn"
            :disabled="testingId === rule.id"
            @click="testRule(rule)"
          >
            <Send :size="13" aria-hidden="true" />
            {{ testingId === rule.id ? "发送中…" : "发送测试" }}
          </button>
          <button type="button" class="navbtn" @click="openEditModal(rule)">
            <Edit3 :size="13" aria-hidden="true" />
            编辑
          </button>
          <button type="button" class="navbtn danger" @click="removeRule(rule)">
            <Trash2 :size="13" aria-hidden="true" />
            删除
          </button>
        </div>
      </article>
    </div>

    <!-- 近期推送日志 -->
    <div class="stack-sm">
      <h3 class="section-subhead">近期推送日志</h3>
      <div class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>规则</th>
              <th>事件 / 舰船</th>
              <th>状态</th>
              <th>重试 / 耗时</th>
              <th>诊断信息</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="logs.length === 0">
              <td colspan="6" class="muted text-center" style="padding: 24px">
                暂无推送历史记录
              </td>
            </tr>
            <tr v-for="log in logs" :key="log.id">
              <td class="muted cell-nowrap">
                {{ formatUtcTime(log.created_at) }}
              </td>
              <td style="font-weight: 550">{{ log.rule_name }}</td>
              <td>
                <span class="pill neutral pill-lead">{{ log.event_type }}</span>
                {{ log.ship_name }}
              </td>
              <td>
                <span class="pill" :class="log.success ? 'good' : 'bad'">
                  {{ log.success ? (log.status_code ?? 200) : (log.status_code ?? "ERR") }}
                </span>
              </td>
              <td class="mono cell-nowrap">
                {{ log.attempt_count }} 次 · {{ log.duration_ms }}ms
              </td>
              <td class="muted cell-diag">
                {{ log.error_message || "-" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 规则编辑弹窗 -->
    <div v-if="modalOpen" class="modal-backdrop" @click.self="modalOpen = false">
      <div
        class="modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="webhook-modal-title"
      >
        <div class="modal-head">
          <h3 id="webhook-modal-title">{{ editingId ? "编辑 Webhook 规则" : "新增 Webhook 规则" }}</h3>
          <button
            type="button"
            class="navbtn btn-icon"
            aria-label="关闭弹窗"
            @click="modalOpen = false"
          >
            <X :size="15" aria-hidden="true" />
          </button>
        </div>

        <form class="form-stack" @submit.prevent="saveRule">
          <div class="form-grid-2">
            <label class="field-label">
              规则名称
              <input
                v-model="form.name"
                name="name"
                required
                placeholder="例如：高价值 WBCCU 飞书群"
                class="input"
              />
            </label>
            <label class="field-label">
              消息模板平台
              <select v-model="form.provider_type" name="provider_type" class="select">
                <option value="generic">标准通用 JSON</option>
                <option value="feishu">飞书交互卡片</option>
                <option value="wecom">企业微信 Markdown</option>
                <option value="discord">Discord Embed</option>
                <option value="custom">自定义 JSON 模板</option>
              </select>
            </label>
          </div>

          <div class="sub-box">
            <strong style="font-size: 13px">推送目标与自定义请求头</strong>
            <div class="url-method-grid">
              <label class="field-label">
                方法
                <select v-model="form.method" name="method" class="select">
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="PATCH">PATCH</option>
                </select>
              </label>
              <label class="field-label">
                Webhook 目标地址
                <input
                  v-model="form.url"
                  name="url"
                  type="url"
                  required
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…"
                  class="input"
                />
              </label>
            </div>

            <div class="stack-sm">
              <div class="row-between">
                <span class="muted cell-meta">自定义 HTTP 请求头（敏感值已脱敏）</span>
                <button type="button" class="link" @click="addHeaderRow">+ 添加 Header</button>
              </div>
              <div
                v-for="(pair, idx) in form.headers"
                :key="idx"
                class="header-pair-row"
              >
                <input
                  v-model="pair.key"
                  aria-label="Header 键名"
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="Header 键名"
                  class="input"
                />
                <input
                  v-model="pair.value"
                  aria-label="Header 值"
                  autocomplete="off"
                  spellcheck="false"
                  placeholder="Header 值（保留 ****** 即沿用原密钥）"
                  class="input"
                />
                <button
                  type="button"
                  class="navbtn danger btn-icon"
                  aria-label="删除此请求头"
                  @click="removeHeaderRow(idx)"
                >
                  <X :size="14" aria-hidden="true" />
                </button>
              </div>
            </div>
          </div>

          <label v-if="form.provider_type === 'custom'" class="field-label">
            自定义 JSON 模板（支持 &#123;&#123;ship_name_zh&#125;&#125;、&#123;&#123;ship_name_en&#125;&#125;、&#123;&#123;sale_type&#125;&#125;、&#123;&#123;edition_label&#125;&#125;、&#123;&#123;discounted_price&#125;&#125;、&#123;&#123;savings&#125;&#125;、&#123;&#123;image_url&#125;&#125;、&#123;&#123;nearest_summary&#125;&#125;）
            <textarea
              v-model="form.custom_template"
              name="custom_template"
              rows="3"
              spellcheck="false"
              class="textarea mono"
            />
          </label>

          <div class="sub-box">
            <strong style="font-size: 13px">通知事件类型（至少选择一项）</strong>
            <div class="event-check-grid" role="group" aria-label="通知事件类型">
              <label
                v-for="opt in WEBHOOK_EVENT_OPTIONS"
                :key="opt.value"
                class="event-check-item"
              >
                <input v-model="form.events" type="checkbox" :value="opt.value" />
                <span class="event-check-meta">
                  <strong>{{ opt.label }}</strong>
                  <span>{{ opt.desc }}</span>
                </span>
              </label>
            </div>
          </div>

          <div class="sub-box">
            <strong style="font-size: 13px">触发过滤条件（非空项按 AND 交集）</strong>

            <label class="field-label">
              双语船名关键词（逗号分隔，内部按 OR 模糊匹配中英文船名）
              <input
                v-model="form.keywordsText"
                name="ship_keywords"
                autocomplete="off"
                placeholder="例如：Zeus, 宙斯, Polaris, 北极星"
                class="input"
              />
            </label>

            <div class="form-grid-5">
              <label class="field-label">
                最低原价 ($)
                <input v-model="form.min_original_price" name="min_original_price" type="number" min="0" step="0.01" class="input" />
              </label>
              <label class="field-label">
                最高原价 ($)
                <input v-model="form.max_original_price" name="max_original_price" type="number" min="0" step="0.01" class="input" />
              </label>
              <label class="field-label">
                最低优惠价 ($)
                <input v-model="form.min_wb_price" name="min_wb_price" type="number" min="0" step="0.01" class="input" />
              </label>
              <label class="field-label">
                最高优惠价 ($)
                <input v-model="form.max_wb_price" name="max_wb_price" type="number" min="0" step="0.01" class="input" />
              </label>
              <label class="field-label">
                最低立省 ($)
                <input v-model="form.min_savings" name="min_savings" type="number" min="0" step="0.01" class="input" />
              </label>
            </div>
          </div>

          <div class="modal-foot">
            <button type="button" class="navbtn" @click="modalOpen = false">取消</button>
            <button type="submit" class="navbtn primary" :disabled="saving">
              {{ saving ? "保存中…" : "保存规则" }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </section>
</template>
