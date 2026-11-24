<script setup lang="ts">
import { onMounted, ref } from "vue";
import { Check, Edit3, Languages, RefreshCw, Save, X } from "lucide-vue-next";
import { apiClient } from "../api/client";
import {
  appStore,
  ensureWriteAuthorized,
  formatShipDisplay,
  notify,
  refreshBootstrapState,
} from "../composables/useAppStore";
import type { ShipItem } from "../types/api";

const loading = ref(false);
const syncing = ref(false);
const savingSource = ref(false);
const items = ref<ShipItem[]>([]);
const queryText = ref("");
const alwaysOnly = ref(false);
const editingShipId = ref<number | null>(null);
const editZhInput = ref("");
const savingEdit = ref(false);
const i18nSourceUrl = ref("");
const savedI18nSourceUrl = ref("");

async function loadShips(): Promise<void> {
  loading.value = true;
  try {
    const resp = await apiClient.listShips({
      q: queryText.value.trim() || undefined,
      always_available_only: alwaysOnly.value,
    });
    items.value = resp.items;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载舰船词典失败", "error");
  } finally {
    loading.value = false;
  }
}

async function loadDictionarySettings(): Promise<void> {
  try {
    const cfg = await apiClient.getSettings();
    i18nSourceUrl.value = cfg.i18n_source_url || "";
    savedI18nSourceUrl.value = i18nSourceUrl.value;
  } catch {
    // 忽略词典源地址初始化读取失败
  }
}

async function saveI18nSourceUrl(silent = false): Promise<boolean> {
  if (!ensureWriteAuthorized()) {
    return false;
  }
  savingSource.value = true;
  try {
    const cleaned = i18nSourceUrl.value.trim();
    const updated = await apiClient.updateSettings({ i18n_source_url: cleaned });
    i18nSourceUrl.value = updated.i18n_source_url;
    savedI18nSourceUrl.value = updated.i18n_source_url;
    if (!silent) {
      notify("远程汉化词典源 URL 已保存", "success");
    }
    return true;
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存远程汉化词典源失败", "error");
    return false;
  } finally {
    savingSource.value = false;
  }
}

async function handleSyncI18n(): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  syncing.value = true;
  try {
    if (i18nSourceUrl.value.trim() !== savedI18nSourceUrl.value.trim()) {
      const saved = await saveI18nSourceUrl(true);
      if (!saved) {
        return;
      }
    }
    const res = await apiClient.syncI18n();
    notify(
      `汉化同步完成：远程拉取 ${res.remote_entries} 条，更新 ${res.updated_ships}/${res.total_ships} 艘舰船`,
      "success"
    );
    await Promise.all([loadShips(), refreshBootstrapState()]);
  } catch (err) {
    notify(err instanceof Error ? err.message : "汉化同步失败", "error");
  } finally {
    syncing.value = false;
  }
}

function startEdit(ship: ShipItem): void {
  editingShipId.value = ship.id;
  editZhInput.value = ship.name_zh;
}

function cancelEdit(): void {
  editingShipId.value = null;
  editZhInput.value = "";
}

async function saveOverride(ship: ShipItem): Promise<void> {
  if (!ensureWriteAuthorized()) {
    return;
  }
  savingEdit.value = true;
  try {
    const res = await apiClient.overrideShipI18n(ship.id, editZhInput.value.trim());
    ship.name_zh = res.name_zh;
    ship.auto_translated = res.auto_translated;
    editingShipId.value = null;
    notify(
      editZhInput.value.trim()
        ? `已保存「${ship.name_en}」人工中文译名`
        : `已恢复「${ship.name_en}」默认词典译名`,
      "success"
    );
    await refreshBootstrapState();
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存中文译名失败", "error");
  } finally {
    savingEdit.value = false;
  }
}

onMounted(() => {
  void Promise.all([loadShips(), loadDictionarySettings()]);
});
</script>

<template>
  <section class="settings-section">
    <div class="panel stack-md">
      <div class="panel-header">
        <div>
          <h2 class="panel-title">舰船双语词典管理</h2>
          <p class="panel-sub">
            支持配置远程词典源并同步社区词典或 global.ini，同时支持点击右侧「编辑」为特定舰船写入最高优先级人工译名（留空保存即恢复默认）。
          </p>
        </div>
        <button type="button" class="navbtn" :disabled="syncing" @click="handleSyncI18n">
          <RefreshCw :size="14" :class="{ 'spin-icon': syncing }" aria-hidden="true" />
          <Languages :size="14" aria-hidden="true" />
          {{ syncing ? "同步中…" : "同步远程词典" }}
        </button>
      </div>

      <form class="bar" @submit.prevent="saveI18nSourceUrl(false)">
        <input
          v-model="i18nSourceUrl"
          name="i18n_source_url"
          type="url"
          autocomplete="off"
          spellcheck="false"
          aria-label="远程汉化词典源 URL"
          placeholder="远程汉化词典源 URL（支持 JSON 映射表或 StarCitizen global.ini）"
          class="search-input"
        />
        <button type="submit" class="navbtn" :disabled="savingSource">
          <Save :size="14" aria-hidden="true" />
          {{ savingSource ? "保存中…" : "保存词典源" }}
        </button>
      </form>

      <form class="bar" @submit.prevent="loadShips">
        <input
          v-model="queryText"
          name="q"
          type="search"
          autocomplete="off"
          spellcheck="false"
          aria-label="检索舰船词典"
          placeholder="检索中文/英文名、制造商…"
          class="search-input"
        />
        <button type="submit" class="navbtn primary">搜索</button>
      </form>

      <div class="row-between">
        <button
          type="button"
          class="chip"
          :aria-pressed="alwaysOnly"
          @click="alwaysOnly = !alwaysOnly; loadShips()"
        >
          仅看常驻可买底船
        </button>
        <span class="muted cell-meta mono">
          共 {{ items.length }} 艘舰船
        </span>
      </div>
    </div>

    <div v-if="loading" class="panel muted panel-loading">
      正在加载舰船词典…
    </div>

    <p v-else-if="items.length === 0" class="empty">
      暂无收录舰船数据，请先同步 RSI 目录。
    </p>

    <div v-else class="table-card">
      <table class="data-table">
        <thead>
          <tr>
            <th>展示效果</th>
            <th>英文原名</th>
            <th>中文译名（人工覆盖优先级最高）</th>
            <th>制造商 / 定位</th>
            <th>估值</th>
            <th>商店状态</th>
            <th class="align-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ship in items" :key="ship.id">
            <td style="font-weight: 600">
              {{ formatShipDisplay(ship.name_en, ship.name_zh, appStore.languageMode) }}
            </td>
            <td class="muted">{{ ship.name_en }}</td>
            <td>
              <div v-if="editingShipId === ship.id" class="inline-edit-row">
                <input
                  v-model="editZhInput"
                  name="name_zh"
                  autocomplete="off"
                  aria-label="人工中文译名"
                  placeholder="留空保存恢复自动"
                  class="input input-compact"
                  @keyup.enter="saveOverride(ship)"
                  @keyup.esc="cancelEdit"
                />
                <button
                  type="button"
                  class="navbtn primary btn-icon"
                  :disabled="savingEdit"
                  aria-label="保存译名"
                  @click="saveOverride(ship)"
                >
                  <Check :size="13" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  class="navbtn btn-icon"
                  aria-label="取消编辑"
                  @click="cancelEdit"
                >
                  <X :size="13" aria-hidden="true" />
                </button>
              </div>
              <template v-else>
                <span v-if="ship.name_zh">{{ ship.name_zh }}</span>
                <span v-else class="muted">未映射</span>
                <span v-if="ship.auto_translated" class="pill info pill-inline">
                  AI 翻译
                </span>
              </template>
            </td>
            <td class="muted cell-meta">
              {{ ship.manufacturer || "-" }}
              <template v-if="ship.focus"> · {{ ship.focus }}</template>
            </td>
            <td class="mono">{{ ship.value > 0 ? `$${ship.value}` : "-" }}</td>
            <td>
              <span v-if="ship.always_available" class="pill info">常驻可买</span>
              <span v-else-if="ship.in_store" class="pill good">当前在售</span>
              <span v-else class="pill neutral">限时/未在售</span>
            </td>
            <td class="align-right">
              <button
                type="button"
                class="navbtn btn-sm"
                @click="startEdit(ship)"
              >
                <Edit3 :size="12" aria-hidden="true" />
                编辑
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
