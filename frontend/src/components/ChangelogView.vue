<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { apiClient } from "../api/client";
import {
  appStore,
  formatAgo,
  formatShipDisplay,
  formatUtcTime,
  notify,
} from "../composables/useAppStore";
import type { ChangelogItem } from "../types/api";

const loading = ref(false);
const items = ref<ChangelogItem[]>([]);
const total = ref(0);

const query = reactive({
  eventType: "",
  q: "",
  limit: 80,
  offset: 0,
});

const EVENT_LABELS: Record<string, { label: string; tone: string }> = {
  "ccu.listed": { label: "CCU 上架", tone: "good" },
  "ccu.relisted": { label: "CCU 返场", tone: "info" },
  "ccu.price_changed": { label: "CCU 调价", tone: "warn" },
  "ccu.delisted": { label: "普通 CCU 下架", tone: "bad" },
  "store.new": { label: "单船上架", tone: "good" },
  "store.back": { label: "单船返场", tone: "good" },
  "store.restock": { label: "重新补货", tone: "good" },
  "store.sold_out": { label: "库存售罄", tone: "bad" },
  "store.gone": { label: "整船下架", tone: "bad" },
  "store.price_changed": { label: "单船调价", tone: "info" },
};

async function loadChangelog(): Promise<void> {
  loading.value = true;
  try {
    const resp = await apiClient.listChangelog({
      limit: query.limit,
      offset: query.offset,
      event_type: query.eventType || undefined,
      q: query.q.trim() || undefined,
    });
    items.value = resp.items;
    total.value = resp.total;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载变动日志失败", "error");
  } finally {
    loading.value = false;
  }
}

function eventMeta(row: ChangelogItem) {
  if (row.event_type === "ccu.delisted") {
    return row.is_warbond
      ? { label: "WBCCU 下架", tone: "bad" }
      : { label: "普通 CCU 下架", tone: "bad" };
  }
  return EVENT_LABELS[row.event_type] || { label: row.event_type, tone: "info" };
}

onMounted(() => {
  void loadChangelog();
});
</script>

<template>
  <section class="stack-md">
    <form class="controls" @submit.prevent="query.offset = 0; loadChangelog()">
      <div class="bar bar-wrap">
        <input
          v-model="query.q"
          name="q"
          type="search"
          autocomplete="off"
          spellcheck="false"
          aria-label="搜索变动记录"
          placeholder="搜索舰船中英文名称或版本…"
          class="search-input"
        />
        <select
          v-model="query.eventType"
          aria-label="按变动类型筛选"
          class="navselect"
          @change="query.offset = 0; loadChangelog()"
        >
          <option value="">全部变动类型</option>
          <option value="ccu.listed">CCU 上架</option>
          <option value="ccu.relisted">CCU 返场</option>
          <option value="ccu.price_changed">CCU 调价</option>
          <option value="ccu.wb_delisted">WBCCU 下架</option>
          <option value="ccu.standard_delisted">普通 CCU 下架</option>
          <option value="store.new">单船上架</option>
          <option value="store.back">单船返场</option>
          <option value="store.sold_out">库存售罄</option>
          <option value="store.gone">整船下架</option>
          <option value="store.price_changed">单船调价</option>
        </select>
        <button type="submit" class="navbtn primary">查询</button>
      </div>
      <div class="count">
        <span>共 {{ total }} 条历史变动记录</span>
      </div>
    </form>

    <div v-if="loading" class="panel muted panel-loading">
      正在加载变动记录…
    </div>

    <p v-else-if="items.length === 0" class="empty">
      暂无变动记录。自系统开始监控（{{
        appStore.storeData.tracking_since
          ? formatAgo(appStore.storeData.tracking_since)
          : "最近"
      }}）起的新上架、售罄、调价与下架事件将按时间倒序显示于此。
    </p>

    <ul v-else class="changes">
      <li v-for="row in items" :key="row.id">
        <time :title="formatUtcTime(row.occurred_at)">{{ formatAgo(row.occurred_at) }}</time>
        <span class="pill" :class="eventMeta(row).tone">
          {{ eventMeta(row).label }}
        </span>
        <span class="nm">
          <span class="nm-title">
            {{ formatShipDisplay(row.ship_name_en, row.ship_name_zh, appStore.languageMode) }}
          </span>
          <span class="muted cell-sub pill-inline nm-edition">
            {{ row.is_warbond ? `Warbond · ${row.edition_label}` : row.edition_label }}
          </span>
        </span>
        <span class="amt">
          <template v-if="row.old_price !== null && row.new_price !== null">
            <s>${{ row.old_price }}</s>${{ row.new_price }}
          </template>
          <template v-else-if="row.new_price !== null">
            ${{ row.new_price }}
            <span v-if="row.original_price && row.original_price > row.new_price" class="muted">
              (原 ${{ row.original_price }})
            </span>
          </template>
          <template v-else-if="row.old_price !== null">
            原 ${{ row.old_price }}
          </template>
          <template v-else>-</template>
        </span>
      </li>
    </ul>
  </section>
</template>
