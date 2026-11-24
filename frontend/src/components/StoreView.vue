<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Search, SlidersHorizontal, X } from "lucide-vue-next";
import { apiClient } from "../api/client";
import {
  appStore,
  formatShipDisplay,
  moneyFromCents,
} from "../composables/useAppStore";
import ShipDetails from "./ShipDetails.vue";
import ShipRow from "./ShipRow.vue";
import type { StoreShip } from "../types/api";

const props = defineProps<{
  viewMode: "store" | "all";
}>();

const emit = defineEmits<{
  (e: "planRoute", payload: { fromKey: string; toRef: number }): void;
}>();

type FilterGroup = "editions" | "sold" | "upgrades" | "game" | "ship";

interface FilterDef {
  key: string;
  label: string;
  group: FilterGroup;
  match: (ship: StoreShip, costsMap: Record<string, number> | null, fromKey: string) => boolean;
}

const SIZES = ["Vehicle", "Snub", "Small", "Medium", "Large", "Capital"];
const SIZE_LABELS: Record<string, string> = {
  Vehicle: "地面载具 (Vehicle)",
  Snub: "微型艇 (Snub)",
  Small: "小型 (Small)",
  Medium: "中型 (Medium)",
  Large: "大型 (Large)",
  Capital: "主力舰 (Capital)",
};

const GROUPS: [FilterGroup, string][] = [
  ["editions", "单船在售版本"],
  ["sold", "官方售卖规律"],
  ["upgrades", "舰船升级 (CCU)"],
  ["game", "游戏内获取 (aUEC)"],
  ["ship", "舰船属性与厂商"],
];

const UPGRADE_FILTERS = new Set(["deals", "ccu"]);

function visibleUpgrades(
  ship: StoreShip,
  costsMap: Record<string, number> | null,
  fromKey: string
) {
  return (ship.upgrades || []).filter(
    (u) => !costsMap || (fromKey !== ship.key && String(u.sku) in costsMap)
  );
}

const FILTERS: FilterDef[] = [
  {
    key: "warbond",
    label: "单船 Warbond",
    group: "editions",
    match: (s) => s.editions.some((e) => e.in_store && e.label === "Warbond"),
  },
  {
    key: "anniversary",
    label: "10 Year",
    group: "editions",
    match: (s) => s.editions.some((e) => e.in_store && e.label === "10 Year"),
  },
  {
    key: "lti",
    label: "终身保险 (LTI)",
    group: "editions",
    match: (s) => s.editions.some((e) => e.in_store && e.insurance === "Lifetime"),
  },
  {
    key: "sale",
    label: "单船标价折扣",
    group: "editions",
    match: (s) =>
      s.editions.some((e) => e.in_store && e.list_price !== null && e.price < e.list_price),
  },
  {
    key: "limited",
    label: "限量库存",
    group: "editions",
    match: (s) => s.editions.some((e) => e.in_store && e.limited),
  },
  {
    key: "always",
    label: "常驻在售",
    group: "sold",
    match: (s) => s.sold === "always",
  },
  {
    key: "saleonly",
    label: "仅限大促 / 限量",
    group: "sold",
    match: (s) => s.sold === "time" || s.sold === "quantity",
  },
  {
    key: "deals",
    label: "仅 WBCCU（Warbond 升级）",
    group: "upgrades",
    match: (s, c, f) => visibleUpgrades(s, c, f).some((u) => Boolean(u.is_warbond)),
  },
  {
    key: "ccu",
    label: "可升级 (CCU 在售)",
    group: "upgrades",
    match: (s, c, f) => visibleUpgrades(s, c, f).length > 0,
  },
  {
    key: "buyable",
    label: "游戏内可买",
    group: "game",
    match: (s) => s.buy.length > 0,
  },
  {
    key: "rentable",
    label: "游戏内可租",
    group: "game",
    match: (s) => s.rent.length > 0,
  },
  {
    key: "gold",
    label: "金牌标准",
    group: "ship",
    match: (s) => s.gold?.status === "done",
  },
  {
    key: "concept",
    label: "概念 / 未实装",
    group: "ship",
    match: (s) => Boolean(s.status && s.status !== "Flight ready"),
  },
];

function shipPriceCents(s: StoreShip): number {
  const inStoreEd = s.editions.filter((e) => e.in_store && e.price > 0).map((e) => e.price);
  if (inStoreEd.length) {
    return Math.min(...inStoreEd);
  }
  if (!s.editions.some((e) => e.in_store) && s.upgrades.length) {
    return Math.min(...s.upgrades.map((u) => u.price));
  }
  return 0;
}

function lowAuec(list: [number, string][]): number {
  return list.length ? Math.min(...list.map((r) => r[0])) : 0;
}

function pledgeDollars(s: StoreShip): number {
  const c = shipPriceCents(s);
  if (c > 0) {
    return c / 100;
  }
  return s.history.length ? s.history[s.history.length - 1][1] : 0;
}

function auecPerDollar(s: StoreShip): number {
  const b = lowAuec(s.buy);
  const d = pledgeDollars(s);
  return b && d ? b / d : 0;
}

function compareReleasePatch(a: StoreShip, b: StoreShip): number {
  const pa = a.released ? a.released.split(".").map(Number) : null;
  const pb = b.released ? b.released.split(".").map(Number) : null;
  if (!pa || !pb) {
    return (pa ? -1 : 0) + (pb ? 1 : 0);
  }
  const maxLen = Math.max(pa.length, pb.length);
  for (let i = 0; i < maxLen; i += 1) {
    const diff = (pb[i] || 0) - (pa[i] || 0);
    if (diff !== 0) {
      return diff;
    }
  }
  return 0;
}

const SORTS: Record<string, { label: string; cmp: (a: StoreShip, b: StoreShip) => number }> = {
  "price-desc": {
    label: "价格：从高到低",
    cmp: (a, b) => shipPriceCents(b) - shipPriceCents(a),
  },
  "price-asc": {
    label: "价格：从低到高",
    cmp: (a, b) => (shipPriceCents(a) || 1e12) - (shipPriceCents(b) || 1e12),
  },
  released: {
    label: "实装版本：最新优先",
    cmp: compareReleasePatch,
  },
  name: {
    label: "舰船名称",
    cmp: () => 0,
  },
  size: {
    label: "舰船体型：从大到小",
    cmp: (a, b) => SIZES.indexOf(b.size) - SIZES.indexOf(a.size),
  },
  game: {
    label: "游戏内售价：从低到高",
    cmp: (a, b) => (lowAuec(a.buy) || 1e15) - (lowAuec(b.buy) || 1e15),
  },
  "game-desc": {
    label: "游戏内售价：从高到低",
    cmp: (a, b) => (lowAuec(b.buy) || -1) - (lowAuec(a.buy) || -1),
  },
  value: {
    label: "性价比：每美元最高 aUEC",
    cmp: (a, b) => auecPerDollar(b) - auecPerDollar(a),
  },
  pilot: {
    label: "主驾火力：从高到低",
    cmp: (a, b) => (b.weapons?.pilot[0] ?? -1) - (a.weapons?.pilot[0] ?? -1),
  },
};

const RATE_STORAGE_KEY = "ccujailer_auec_rate";

function loadSavedRate(): number {
  try {
    return Number(localStorage.getItem(RATE_STORAGE_KEY)) || 500000;
  } catch {
    return 500000;
  }
}

const searchQ = ref("");
const activeFilters = ref<string[]>([]);
const selectedMaker = ref("");
const selectedSize = ref("");
const sortKey = ref("price-desc");
const fromShipKey = ref("");
const auecRate = ref(loadSavedRate());
const sheetDialogRef = ref<HTMLDialogElement | null>(null);
const fromSelectRef = ref<HTMLSelectElement | null>(null);
const detailShip = ref<StoreShip | null>(null);
const plannerEnabled = computed(() => Boolean(appStore.authStatus.upgrade_planner_enabled));

const lookupState = ref<"" | "loading" | "ok" | "error">("");
const lookupCostsMap = ref<Record<string, number> | null>(null);
const lookupRefId = ref<number | null>(null);

const allShips = computed(() => appStore.storeData.ships);
const inStoreShips = computed(() => allShips.value.filter((s) => s.in_store));
const sellableShips = computed(() =>
  allShips.value.filter((s) => s.in_store || s.upgrades.length > 0)
);
const makersList = computed(() =>
  [...new Set(allShips.value.map((s) => s.maker).filter(Boolean))].sort()
);

const fromShip = computed(() => allShips.value.find((s) => s.key === fromShipKey.value) || null);
const currentRefId = computed(() => fromShip.value?.ccu?.ref ?? null);

async function fetchUpgradeCostsForRef(refId: number): Promise<void> {
  lookupRefId.value = refId;
  lookupState.value = "loading";
  lookupCostsMap.value = null;
  try {
    const resp = await apiClient.getUpgradeCosts(refId);
    if (currentRefId.value === refId) {
      lookupCostsMap.value = resp.costs;
      lookupState.value = "ok";
    }
  } catch {
    if (currentRefId.value === refId) {
      lookupCostsMap.value = null;
      lookupState.value = "error";
    }
  }
}

watch(currentRefId, (refId) => {
  if (!refId) {
    lookupState.value = "";
    lookupCostsMap.value = null;
    lookupRefId.value = null;
    return;
  }
  void fetchUpgradeCostsForRef(refId);
});

const upgradableOwnedOptions = computed(() => {
  const list: { key: string; label: string; valueCents: number }[] = [];
  for (const s of allShips.value) {
    if (s.ccu && s.ccu.value > 0) {
      list.push({
        key: s.key,
        label: `${formatShipDisplay(s.name_en, s.name_zh, appStore.languageMode)} (${moneyFromCents(s.ccu.value)})`,
        valueCents: s.ccu.value,
      });
      continue;
    }
    const std = s.editions.find((e) => e.in_store && e.label === "Standard" && !e.modules.length);
    if (std) {
      list.push({
        key: s.key,
        label: `${formatShipDisplay(s.name_en, s.name_zh, appStore.languageMode)} (${moneyFromCents(std.price)})`,
        valueCents: std.price,
      });
      continue;
    }
    const lastHist = (s.history || []).filter((h) => !h[2]).pop();
    if (lastHist) {
      const valCents = Math.round(lastHist[1] * 100);
      list.push({
        key: s.key,
        label: `${formatShipDisplay(s.name_en, s.name_zh, appStore.languageMode)} ($${lastHist[1].toLocaleString("en-US")})`,
        valueCents: valCents,
      });
    }
  }
  return list.sort((a, b) =>
    a.label.localeCompare(b.label, "zh-CN", { numeric: true, sensitivity: "base" })
  );
});

const fromPriceCents = computed(
  () => upgradableOwnedOptions.value.find((o) => o.key === fromShipKey.value)?.valueCents || 0
);

const effectiveCosts = computed(() =>
  lookupState.value === "ok" && lookupRefId.value === currentRefId.value
    ? lookupCostsMap.value
    : null
);

const filterCounts = computed(() => {
  const map: Record<string, number> = {};
  for (const f of FILTERS) {
    const pool = UPGRADE_FILTERS.has(f.key) ? sellableShips.value : inStoreShips.value;
    map[f.key] = pool.filter((s) =>
      f.match(s, effectiveCosts.value, fromShipKey.value)
    ).length;
  }
  return map;
});

function normText(raw: string): string {
  return raw.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, " ").trim();
}

const shownShips = computed(() => {
  const q = normText(searchQ.value);
  const activeDefs = FILTERS.filter((f) => activeFilters.value.includes(f.key));
  const pool =
    props.viewMode === "all"
      ? allShips.value
      : activeDefs.some((d) => UPGRADE_FILTERS.has(d.key))
        ? sellableShips.value
        : inStoreShips.value;

  const filtered = pool
    .filter((s) => {
      if (!q) {
        return true;
      }
      const hay = normText(`${s.name_en} ${s.name_zh} ${s.maker} ${s.maker_code} ${s.role}`);
      return hay.includes(q);
    })
    .filter((s) =>
      activeDefs.every((d) => d.match(s, effectiveCosts.value, fromShipKey.value))
    )
    .filter((s) => !selectedMaker.value || s.maker === selectedMaker.value)
    .filter((s) => !selectedSize.value || s.size === selectedSize.value)
    .slice();

  const sorter = SORTS[sortKey.value] || SORTS["price-desc"];
  return filtered.sort((a, b) => sorter.cmp(a, b) || a.name_en.localeCompare(b.name_en));
});

const activeChipPills = computed(() => {
  const list: { id: string; label: string; remove: () => void }[] = [];
  if (fromShip.value) {
    list.push({
      id: "from",
      label: `底船: ${formatShipDisplay(fromShip.value.name_en, fromShip.value.name_zh, appStore.languageMode)}`,
      remove: () => {
        fromShipKey.value = "";
      },
    });
  }
  for (const f of FILTERS) {
    if (activeFilters.value.includes(f.key)) {
      list.push({
        id: f.key,
        label: f.label,
        remove: () => toggleFilter(f.key),
      });
    }
  }
  if (selectedMaker.value) {
    list.push({
      id: "maker",
      label: selectedMaker.value,
      remove: () => {
        selectedMaker.value = "";
      },
    });
  }
  if (selectedSize.value) {
    list.push({
      id: "size",
      label: SIZE_LABELS[selectedSize.value] || selectedSize.value,
      remove: () => {
        selectedSize.value = "";
      },
    });
  }
  return list;
});

const hasAnyFilter = computed(
  () =>
    Boolean(
      searchQ.value ||
        activeFilters.value.length ||
        selectedMaker.value ||
        selectedSize.value ||
        fromShipKey.value
    )
);

function toggleFilter(key: string): void {
  const idx = activeFilters.value.indexOf(key);
  if (idx === -1) {
    activeFilters.value.push(key);
  } else {
    activeFilters.value.splice(idx, 1);
  }
}

function clearAllFilters(): void {
  searchQ.value = "";
  activeFilters.value = [];
  selectedMaker.value = "";
  selectedSize.value = "";
  fromShipKey.value = "";
}

function openFiltersSheet(focusFrom = false): void {
  sheetDialogRef.value?.showModal();
  if (focusFrom) {
    window.setTimeout(() => fromSelectRef.value?.focus(), 30);
  }
}

function closeFiltersSheet(): void {
  sheetDialogRef.value?.close();
}

function onDialogBackdropClick(event: MouseEvent): void {
  if (event.target === sheetDialogRef.value) {
    closeFiltersSheet();
  }
}

function handleRateInput(event: Event): void {
  const digits = (event.target as HTMLInputElement).value.replace(/[^0-9]/g, "");
  const n = Math.max(1000, Number(digits) || 500000);
  auecRate.value = n;
  try {
    localStorage.setItem(RATE_STORAGE_KEY, String(n));
  } catch {
    // 忽略浏览器存储异常
  }
}

function openDetailsModal(ship: StoreShip): void {
  detailShip.value = ship;
}

function closeDetailsModal(): void {
  detailShip.value = null;
}

function handleStoreKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && detailShip.value) {
    closeDetailsModal();
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleStoreKeydown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleStoreKeydown);
});
</script>

<template>
  <div>
    <!-- 原版 Controls 工具栏（搜索、Filters 弹框按钮、已选标签行、数量统计 + 排序下拉） -->
    <div class="controls">
      <div class="bar">
        <div class="search-wrap">
          <Search :size="16" class="search-icon" aria-hidden="true" />
          <input
            id="q"
            v-model="searchQ"
            name="q"
            type="search"
            autocomplete="off"
            spellcheck="false"
            class="has-icon"
            placeholder="搜索中文/英文船名、制造商、定位…"
            aria-label="搜索舰船"
          />
        </div>
        <button
          type="button"
          class="fbtn"
          aria-haspopup="dialog"
          @click="openFiltersSheet(false)"
        >
          <SlidersHorizontal :size="15" aria-hidden="true" />
          筛选
          <span v-if="activeChipPills.length" class="badge">{{ activeChipPills.length }}</span>
        </button>
      </div>

      <div v-if="activeChipPills.length" class="on" aria-label="已生效筛选条件">
        <button
          v-for="pill in activeChipPills"
          :key="pill.id"
          type="button"
          class="chip"
          aria-pressed="true"
          @click="pill.remove()"
        >
          {{ pill.label }} <span aria-hidden="true">✕</span>
        </button>
      </div>

      <p v-if="lookupState === 'loading' && fromShip" class="lookup" aria-live="polite">
        正在向 RSI 升级商店实时查询从 {{ formatShipDisplay(fromShip.name_en, fromShip.name_zh) }} 升级的准确差价…
      </p>
      <p v-else-if="lookupState === 'error' && fromShip" class="lookup bad" aria-live="polite">
        暂时无法连接 RSI 升级接口查询实时差价，部分金额已显示估算参考值 (≈)。
        <button
          type="button"
          class="link"
          @click="currentRefId && fetchUpgradeCostsForRef(currentRefId)"
        >
          重试查询
        </button>
      </p>

      <div class="count">
        <span>共 {{ shownShips.length }} 艘舰船</span>
        <button v-if="hasAnyFilter" type="button" class="link" @click="clearAllFilters">
          清空筛选
        </button>
        <select id="sort" v-model="sortKey" aria-label="排序方式">
          <option v-for="(item, key) in SORTS" :key="key" :value="key">
            {{ item.label }}
          </option>
        </select>
      </div>
    </div>

    <!-- 原版 <dialog class="sheet"> 弹窗 / 手机底部抽屉筛选器 -->
    <dialog
      ref="sheetDialogRef"
      class="sheet"
      aria-label="舰船与 CCU 筛选"
      @click="onDialogBackdropClick"
    >
      <div class="sheet-in">
        <div class="sheet-handle" aria-hidden="true" />
        <div class="sheet-head">
          <h2>筛选条件</h2>
          <button type="button" class="link" @click="closeFiltersSheet">完成</button>
        </div>

        <section v-for="[groupKey, groupTitle] in GROUPS" :key="groupKey">
          <h3>{{ groupTitle }}</h3>

          <label v-if="groupKey === 'upgrades'" class="field">
            我拥有的底船
            <select id="from" ref="fromSelectRef" v-model="fromShipKey">
              <option value="">选择你的底船查看真实升级补差价</option>
              <option v-for="opt in upgradableOwnedOptions" :key="opt.key" :value="opt.key">
                {{ opt.label }}
              </option>
            </select>
          </label>

          <div class="chips" role="group" :aria-label="groupTitle">
            <button
              v-for="f in FILTERS.filter((item) => item.group === groupKey)"
              :key="f.key"
              type="button"
              class="chip"
              :aria-pressed="activeFilters.includes(f.key)"
              @click="toggleFilter(f.key)"
            >
              {{ f.label }}
              <span v-if="viewMode === 'store'" class="n">{{ filterCounts[f.key] }}</span>
            </button>
          </div>

          <p v-if="groupKey === 'sold'" class="muted note">
            售卖规律来源于 Star Citizen Wiki 玩家记录（RSI 官方未公开承诺，仅作活动返场规律参考）。
          </p>

          <label v-if="groupKey === 'game'" class="field rate" for="rate-input">
            我每小时约赚
            <input
              id="rate-input"
              inputmode="numeric"
              :value="auecRate.toLocaleString('en-US')"
              @change="handleRateInput"
            />
            aUEC
          </label>

          <div v-if="groupKey === 'ship'" class="selects">
            <select id="maker" v-model="selectedMaker" aria-label="制造商">
              <option value="">全部制造商</option>
              <option v-for="maker in makersList" :key="maker" :value="maker">
                {{ maker }}
              </option>
            </select>
            <select id="size" v-model="selectedSize" aria-label="舰船体型">
              <option value="">全部尺寸体型</option>
              <option v-for="sz in SIZES" :key="sz" :value="sz">
                {{ SIZE_LABELS[sz] || sz }}
              </option>
            </select>
          </div>
        </section>

        <div class="sheet-foot">
          <button v-if="hasAnyFilter" type="button" class="link" @click="clearAllFilters">
            清除全部
          </button>
          <span v-else />
          <button type="button" class="show" @click="closeFiltersSheet">
            查看 {{ shownShips.length }} 艘舰船
          </button>
        </div>
      </div>
    </dialog>

    <!-- 原版单栏舰船流 -->
    <p v-if="!appStore.storeLoaded && allShips.length === 0" class="empty">
      正在加载商店数据，请稍候…
    </p>

    <ul v-else-if="shownShips.length > 0" class="ships">
      <ShipRow
        v-for="ship in shownShips"
        :key="ship.id"
        :ship="ship"
        :rate="auecRate"
        :from-key="fromShipKey"
        :from-name="fromShip ? formatShipDisplay(fromShip.name_en, fromShip.name_zh) : ''"
        :from-price-cents="fromPriceCents"
        :costs-map="effectiveCosts"
        :is-estimate="!currentRefId || lookupState === 'error'"
        :game-version="appStore.storeData.game_version"
        :by-value-sort="sortKey === 'value'"
        :planner-enabled="plannerEnabled"
        @pick-from-ship="openFiltersSheet(true)"
        @plan-route="emit('planRoute', $event)"
        @open-details="openDetailsModal"
      />
    </ul>

    <p v-else class="empty">
      没有找到符合当前筛选条件的舰船，请尝试放宽筛选或更改搜索关键词。
    </p>

    <!-- 悬浮舰船详情弹出模态框 -->
    <div v-if="detailShip" class="modal-backdrop" @click.self="closeDetailsModal">
      <div
        class="modal-card details-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ship-details-modal-title"
      >
        <div class="modal-head details-modal-head">
          <div class="details-modal-title-group">
            <h3 id="ship-details-modal-title">
              {{ formatShipDisplay(detailShip.name_en, detailShip.name_zh, appStore.languageMode) }}
            </h3>
            <a
              v-if="detailShip.rsi"
              class="navbtn btn-sm"
              :href="detailShip.rsi"
              target="_blank"
              rel="noopener"
            >
              RSI 官方页面 ↗
            </a>
          </div>
          <button
            type="button"
            class="navbtn btn-icon"
            aria-label="关闭详情弹窗"
            @click="closeDetailsModal"
          >
            <X :size="15" aria-hidden="true" />
          </button>
        </div>

        <ShipDetails
          :ship="detailShip"
          :rate="auecRate"
          :game-version="appStore.storeData.game_version"
        />
      </div>
    </div>
  </div>
</template>
