<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ChevronLeft, ChevronRight, Search, SlidersHorizontal, X } from "lucide-vue-next";
import { apiClient } from "../api/client";
import {
  DEFAULT_STORE_PAGE_SIZE,
  appStore,
  formatShipDisplay,
  getEffectiveDefaultStoreFilters,
  getEffectiveDefaultStoreSort,
  moneyFromCents,
  notify,
  updateUserPreferences,
} from "../composables/useAppStore";
import {
  SHIP_SIZES_ORDER,
  formatManufacturer,
  formatShipRoleZh,
  formatShipSizeZh,
  getManufacturerZh,
} from "../utils/localization";
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

const SIZES: readonly string[] = SHIP_SIZES_ORDER;

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
    label: "限时开售 / 限量",
    group: "sold",
    match: (s) => s.sold === "time" || s.sold === "quantity",
  },
  {
    key: "deals",
    label: "仅 WBCCU",
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
    label: "画饼中 / 未可飞",
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
    label: "可飞版本：最新优先",
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
  if (appStore.userPreferences.auec_rate && appStore.userPreferences.auec_rate >= 1000) {
    return appStore.userPreferences.auec_rate;
  }
  try {
    return Number(localStorage.getItem(RATE_STORAGE_KEY)) || 500000;
  } catch {
    return 500000;
  }
}

interface ViewFilterSnapshot {
  searchQ: string;
  activeFilters: string[];
  selectedMaker: string;
  selectedSize: string;
  sortKey: string;
  fromShipKey: string;
  currentPage: number;
}

const initialStoreFilters = props.viewMode === "store" ? getEffectiveDefaultStoreFilters() : [];
const initialStoreSort =
  props.viewMode === "store" ? getEffectiveDefaultStoreSort() : "price-desc";

const savedSnapshots: Record<"store" | "all", ViewFilterSnapshot> = {
  store: {
    searchQ: "",
    activeFilters: getEffectiveDefaultStoreFilters(),
    selectedMaker: "",
    selectedSize: "",
    sortKey: getEffectiveDefaultStoreSort(),
    fromShipKey: "",
    currentPage: 1,
  },
  all: {
    searchQ: "",
    activeFilters: [],
    selectedMaker: "",
    selectedSize: "",
    sortKey: "price-desc",
    fromShipKey: "",
    currentPage: 1,
  },
};
const userEditedStoreDefaults = ref(false);
let restoringSnapshot = false;

const PAGE_SIZE_OPTIONS: { value: number; label: string }[] = [
  { value: 10, label: "10 艘/页" },
  { value: 20, label: "20 艘/页" },
  { value: 30, label: "30 艘/页" },
  { value: 50, label: "50 艘/页" },
  { value: 100, label: "100 艘/页" },
  { value: 0, label: "全部显示" },
];

const searchQ = ref("");
const activeFilters = ref<string[]>([...initialStoreFilters]);
const selectedMaker = ref("");
const selectedSize = ref("");
const sortKey = ref(initialStoreSort);
const fromShipKey = ref("");
const currentPage = ref(1);
const pageSize = ref(
  typeof appStore.userPreferences.store_page_size === "number"
    ? appStore.userPreferences.store_page_size
    : DEFAULT_STORE_PAGE_SIZE
);
const auecRate = ref(loadSavedRate());
const sheetDialogRef = ref<HTMLDialogElement | null>(null);
const fromSelectRef = ref<HTMLSelectElement | null>(null);
const detailShip = ref<StoreShip | null>(null);
const plannerEnabled = computed(() => Boolean(appStore.authStatus.upgrade_planner_enabled));

// 专用「我需要补多少？」补差价弹窗状态
const upgradeCalcShip = ref<StoreShip | null>(null);
const calcFromKey = ref("");
const calcFromSearch = ref("");
const calcLookupState = ref<"" | "loading" | "ok" | "error">("");
const calcLookupCostsMap = ref<Record<string, number> | null>(null);
const calcLookupRefId = ref<number | null>(null);

watch(
  () => props.viewMode,
  (nextMode, prevMode) => {
    if (prevMode && nextMode !== prevMode) {
      savedSnapshots[prevMode] = {
        searchQ: searchQ.value,
        activeFilters: [...activeFilters.value],
        selectedMaker: selectedMaker.value,
        selectedSize: selectedSize.value,
        sortKey: sortKey.value,
        fromShipKey: fromShipKey.value,
        currentPage: currentPage.value,
      };
      const target = savedSnapshots[nextMode];
      restoringSnapshot = true;
      searchQ.value = target.searchQ;
      activeFilters.value = [...target.activeFilters];
      selectedMaker.value = target.selectedMaker;
      selectedSize.value = target.selectedSize;
      sortKey.value = target.sortKey;
      fromShipKey.value = target.fromShipKey;
      currentPage.value = target.currentPage || 1;
      window.setTimeout(() => {
        restoringSnapshot = false;
      }, 0);
    }
  }
);

watch([searchQ, selectedMaker, selectedSize, fromShipKey], () => {
  if (!restoringSnapshot) {
    currentPage.value = 1;
  }
});

watch(
  () => appStore.preferencesSyncVersion,
  () => {
    if (appStore.userPreferences.auec_rate && appStore.userPreferences.auec_rate >= 1000) {
      auecRate.value = appStore.userPreferences.auec_rate;
    }
    if (typeof appStore.userPreferences.store_page_size === "number") {
      pageSize.value = appStore.userPreferences.store_page_size;
    }
    if (!userEditedStoreDefaults.value) {
      const nextFilters = getEffectiveDefaultStoreFilters();
      const nextSort = getEffectiveDefaultStoreSort();
      savedSnapshots.store.activeFilters = [...nextFilters];
      savedSnapshots.store.sortKey = nextSort;
      if (props.viewMode === "store") {
        restoringSnapshot = true;
        activeFilters.value = [...nextFilters];
        sortKey.value = nextSort;
        window.setTimeout(() => {
          restoringSnapshot = false;
        }, 0);
      }
    }
  }
);

const lookupState = ref<"" | "loading" | "ok" | "error">("");
const lookupCostsMap = ref<Record<string, number> | null>(null);
const lookupRefId = ref<number | null>(null);
const revealGeneration = ref(0);

const allShips = computed(() => appStore.storeData.ships);
const inStoreShips = computed(() => allShips.value.filter((s) => s.in_store));
const sellableShips = computed(() =>
  allShips.value.filter((s) => s.in_store || s.upgrades.length > 0)
);
const makersList = computed(() =>
  [...new Set(allShips.value.map((s) => s.maker).filter(Boolean))].sort((a, b) =>
    formatManufacturer(a, "", appStore.languageMode).localeCompare(
      formatManufacturer(b, "", appStore.languageMode),
      "zh-CN",
      { sensitivity: "base" }
    )
  )
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
      const hay = normText(
        `${s.name_en} ${s.name_zh} ${s.maker} ${getManufacturerZh(s.maker, s.maker_code)} ${s.maker_code} ${s.role} ${formatShipRoleZh(s.role)} ${s.size} ${formatShipSizeZh(s.size)}`
      );
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
      label: `可升级船: ${formatShipDisplay(fromShip.value.name_en, fromShip.value.name_zh, appStore.languageMode)}`,
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
      label: formatManufacturer(selectedMaker.value, "", appStore.languageMode),
      remove: () => {
        selectedMaker.value = "";
      },
    });
  }
  if (selectedSize.value) {
    list.push({
      id: "size",
      label: formatShipSizeZh(selectedSize.value),
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
  if (props.viewMode === "store") {
    userEditedStoreDefaults.value = true;
  }
  const idx = activeFilters.value.indexOf(key);
  if (idx === -1) {
    activeFilters.value.push(key);
  } else {
    activeFilters.value.splice(idx, 1);
  }
  currentPage.value = 1;
}

function clearAllFilters(): void {
  if (props.viewMode === "store") {
    userEditedStoreDefaults.value = true;
  }
  searchQ.value = "";
  activeFilters.value = [];
  selectedMaker.value = "";
  selectedSize.value = "";
  fromShipKey.value = "";
  currentPage.value = 1;
}

function handleSortChange(): void {
  if (props.viewMode === "store") {
    userEditedStoreDefaults.value = true;
  }
  currentPage.value = 1;
}

function handlePageSizeChange(): void {
  currentPage.value = 1;
  void updateUserPreferences({ store_page_size: pageSize.value });
}

const totalPages = computed(() => {
  if (pageSize.value <= 0 || shownShips.value.length === 0) {
    return 1;
  }
  return Math.max(1, Math.ceil(shownShips.value.length / pageSize.value));
});

const safeCurrentPage = computed(() => Math.min(Math.max(1, currentPage.value), totalPages.value));

const paginatedShips = computed(() => {
  if (pageSize.value <= 0) {
    return shownShips.value;
  }
  const start = (safeCurrentPage.value - 1) * pageSize.value;
  return shownShips.value.slice(start, start + pageSize.value);
});

const listRevealKey = computed(() =>
  [
    props.viewMode,
    searchQ.value.trim(),
    [...activeFilters.value].sort().join(","),
    selectedMaker.value,
    selectedSize.value,
    sortKey.value,
    fromShipKey.value,
    safeCurrentPage.value,
    pageSize.value,
  ].join("|")
);

watch(listRevealKey, () => {
  revealGeneration.value += 1;
});

const visiblePageNumbers = computed(() => {
  const total = totalPages.value;
  const cur = safeCurrentPage.value;
  if (total <= 7) {
    return Array.from({ length: total }, (_, idx) => idx + 1);
  }
  const pages = new Set<number>([1, total, cur - 1, cur, cur + 1]);
  return [...pages].filter((p) => p >= 1 && p <= total).sort((a, b) => a - b);
});

function goToPage(page: number): void {
  const clamped = Math.min(Math.max(1, page), totalPages.value);
  if (clamped === currentPage.value) {
    return;
  }
  currentPage.value = clamped;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function scrollToTop(): void {
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function saveCurrentFiltersAsPersonalDefault(): Promise<void> {
  if (!appStore.authStatus.authenticated) {
    notify("请先登录后保存个人默认首页筛选条件", "info");
    return;
  }
  await updateUserPreferences(
    {
      default_store_filters: [...activeFilters.value],
      default_store_sort: sortKey.value,
    },
    { immediate: true }
  );
  userEditedStoreDefaults.value = false;
  notify("已将当前筛选与排序设为你的首页默认条件（已同步云端）", "success");
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
  void updateUserPreferences({ auec_rate: n });
}

const calcFromShip = computed(
  () => allShips.value.find((s) => s.key === calcFromKey.value) || null
);
const calcFromRefId = computed(() => calcFromShip.value?.ccu?.ref ?? null);
const calcFromPriceCents = computed(
  () => upgradableOwnedOptions.value.find((o) => o.key === calcFromKey.value)?.valueCents || 0
);
const calcEffectiveCosts = computed(() =>
  calcLookupState.value === "ok" && calcLookupRefId.value === calcFromRefId.value
    ? calcLookupCostsMap.value
    : null
);

const calcTargetMaxValueCents = computed(() => {
  const ship = upgradeCalcShip.value;
  if (!ship) {
    return 0;
  }
  const upgradePrices = ship.upgrades.map((u) => u.price);
  return Math.max(ship.ccu?.value || 0, ...upgradePrices, 0);
});

const calcEligibleOptions = computed(() => {
  const targetShip = upgradeCalcShip.value;
  const maxCents = calcTargetMaxValueCents.value;
  const q = normText(calcFromSearch.value);
  return upgradableOwnedOptions.value.filter((opt) => {
    if (targetShip && opt.key === targetShip.key) {
      return false;
    }
    if (maxCents > 0 && opt.valueCents >= maxCents) {
      return false;
    }
    if (!q) {
      return true;
    }
    const shipObj = allShips.value.find((s) => s.key === opt.key);
    const hay = normText(
      `${opt.label} ${shipObj?.name_en || ""} ${shipObj?.name_zh || ""} ${shipObj?.maker || ""} ${shipObj ? getManufacturerZh(shipObj.maker, shipObj.maker_code) : ""}`
    );
    return hay.includes(q);
  });
});

async function fetchCalcUpgradeCostsForRef(refId: number): Promise<void> {
  if (lookupState.value === "ok" && lookupRefId.value === refId && lookupCostsMap.value) {
    calcLookupRefId.value = refId;
    calcLookupCostsMap.value = lookupCostsMap.value;
    calcLookupState.value = "ok";
    return;
  }
  calcLookupRefId.value = refId;
  calcLookupState.value = "loading";
  calcLookupCostsMap.value = null;
  try {
    const resp = await apiClient.getUpgradeCosts(refId);
    if (calcFromRefId.value === refId) {
      calcLookupCostsMap.value = resp.costs;
      calcLookupState.value = "ok";
    }
  } catch {
    if (calcFromRefId.value === refId) {
      calcLookupCostsMap.value = null;
      calcLookupState.value = "error";
    }
  }
}

watch(calcFromRefId, (refId) => {
  if (!refId || !upgradeCalcShip.value) {
    calcLookupState.value = "";
    calcLookupCostsMap.value = null;
    calcLookupRefId.value = null;
    return;
  }
  void fetchCalcUpgradeCostsForRef(refId);
});

function openUpgradeCalcModal(ship: StoreShip): void {
  upgradeCalcShip.value = ship;
  calcFromSearch.value = "";
  const maxVal = Math.max(ship.ccu?.value || 0, ...ship.upgrades.map((u) => u.price), 0);
  if (
    fromShipKey.value &&
    fromShipKey.value !== ship.key &&
    (maxVal === 0 || fromPriceCents.value < maxVal)
  ) {
    calcFromKey.value = fromShipKey.value;
    if (currentRefId.value) {
      void fetchCalcUpgradeCostsForRef(currentRefId.value);
    }
  } else {
    calcFromKey.value = "";
    calcLookupState.value = "";
    calcLookupCostsMap.value = null;
    calcLookupRefId.value = null;
  }
}

function closeUpgradeCalcModal(): void {
  upgradeCalcShip.value = null;
}

function applyCalcFromToStore(): void {
  if (!calcFromKey.value) {
    return;
  }
  fromShipKey.value = calcFromKey.value;
  if (props.viewMode === "store") {
    userEditedStoreDefaults.value = true;
  }
  closeUpgradeCalcModal();
  notify("已将所选可升级船应用到列表差价显示", "success");
}

function openPlannerFromUpgradeCalc(): void {
  if (!upgradeCalcShip.value?.ccu) {
    return;
  }
  const toRef = upgradeCalcShip.value.ccu.ref;
  const fromK = calcFromKey.value || fromShipKey.value || "";
  closeUpgradeCalcModal();
  emit("planRoute", { fromKey: fromK, toRef });
}

function openDetailsModal(ship: StoreShip): void {
  detailShip.value = ship;
}

function closeDetailsModal(): void {
  detailShip.value = null;
}

function handleStoreKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    if (upgradeCalcShip.value) {
      closeUpgradeCalcModal();
    } else if (detailShip.value) {
      closeDetailsModal();
    }
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
        <button
          type="button"
          class="mobile-sticky-brand"
          aria-label="CCUJailer 返回顶部"
          title="CCUJailer · 返回顶部"
          @click="scrollToTop"
        >
          <img src="/favicon.svg" alt="" aria-hidden="true" />
        </button>
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

      <TransitionGroup
        v-if="activeChipPills.length"
        name="holo-chip"
        tag="div"
        class="on"
        aria-label="已生效筛选条件"
      >
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
      </TransitionGroup>

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
        <span>
          共 {{ shownShips.length }} 艘舰船<template v-if="pageSize > 0 && totalPages > 1"> · 第 {{ safeCurrentPage }}/{{ totalPages }} 页</template>
        </span>
        <button v-if="hasAnyFilter" type="button" class="link" @click="clearAllFilters">
          清空筛选
        </button>
        <div class="count-right">
          <select
            v-model.number="pageSize"
            aria-label="每页显示数量"
            class="page-size-select"
            @change="handlePageSizeChange"
          >
            <option v-for="opt in PAGE_SIZE_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <select id="sort" v-model="sortKey" aria-label="排序方式" @change="handleSortChange">
            <option v-for="(item, key) in SORTS" :key="key" :value="key">
              {{ item.label }}
            </option>
          </select>
        </div>
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
            我拥有的可升级船
            <select id="from" ref="fromSelectRef" v-model="fromShipKey">
              <option value="">选择你的可升级船查看真实升级补差价</option>
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
                {{ formatManufacturer(maker, "", appStore.languageMode) }}
              </option>
            </select>
            <select id="size" v-model="selectedSize" aria-label="舰船体型">
              <option value="">全部尺寸体型</option>
              <option v-for="sz in SIZES" :key="sz" :value="sz">
                {{ formatShipSizeZh(sz) }}
              </option>
            </select>
          </div>
        </section>

        <div class="sheet-foot">
          <div class="sheet-foot-actions">
            <button v-if="hasAnyFilter" type="button" class="link" @click="clearAllFilters">
              清除全部
            </button>
            <button
              v-if="viewMode === 'store' && appStore.authStatus.authenticated"
              type="button"
              class="link"
              @click="saveCurrentFiltersAsPersonalDefault"
            >
              设为我的默认筛选
            </button>
          </div>
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
        v-for="(ship, idx) in paginatedShips"
        :key="`${revealGeneration}-${ship.id}`"
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
        :stagger-index="Math.min(idx, 12)"
        @pick-from-ship="openUpgradeCalcModal"
        @plan-route="emit('planRoute', $event)"
        @open-details="openDetailsModal"
      />
    </ul>

    <nav
      v-if="shownShips.length > 0 && pageSize > 0 && totalPages > 1"
      class="pagination-bar"
      aria-label="舰船列表分页"
    >
      <span class="muted cell-meta">
        显示第 {{ (safeCurrentPage - 1) * pageSize + 1 }}–{{
          Math.min(safeCurrentPage * pageSize, shownShips.length)
        }} 艘，共 {{ shownShips.length }} 艘
      </span>
      <div class="pagination-pages">
        <button
          type="button"
          class="navbtn btn-sm pagination-btn"
          :disabled="safeCurrentPage <= 1"
          aria-label="上一页"
          @click="goToPage(safeCurrentPage - 1)"
        >
          <ChevronLeft :size="14" aria-hidden="true" />
          上一页
        </button>
        <template v-for="(p, idx) in visiblePageNumbers" :key="p">
          <span
            v-if="idx > 0 && p - visiblePageNumbers[idx - 1] > 1"
            class="pagination-ellipsis"
            aria-hidden="true"
          >
            …
          </span>
          <button
            type="button"
            class="navbtn btn-sm pagination-btn"
            :class="{ primary: p === safeCurrentPage }"
            :aria-current="p === safeCurrentPage ? 'page' : undefined"
            @click="goToPage(p)"
          >
            {{ p }}
          </button>
        </template>
        <button
          type="button"
          class="navbtn btn-sm pagination-btn"
          :disabled="safeCurrentPage >= totalPages"
          aria-label="下一页"
          @click="goToPage(safeCurrentPage + 1)"
        >
          下一页
          <ChevronRight :size="14" aria-hidden="true" />
        </button>
      </div>
    </nav>

    <p v-else class="empty">
      没有找到符合当前筛选条件的舰船，请尝试放宽筛选或更改搜索关键词。
    </p>

    <!-- 悬浮舰船详情弹出模态框（居中） -->
    <Transition name="holo-modal">
      <div
        v-if="detailShip"
        class="modal-backdrop"
        @click.self="closeDetailsModal"
      >
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
    </Transition>

    <!-- 专用「我需要补多少？」升级补差价计算对比弹窗 -->
    <Transition name="holo-modal">
      <div v-if="upgradeCalcShip" class="modal-backdrop" @click.self="closeUpgradeCalcModal">
        <div
          class="modal-card upgrade-calc-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="upgrade-calc-modal-title"
        >
          <div class="modal-head">
            <div>
              <h3 id="upgrade-calc-modal-title">
                升级补差价计算 ·
                {{ formatShipDisplay(upgradeCalcShip.name_en, upgradeCalcShip.name_zh, appStore.languageMode) }}
              </h3>
              <p class="muted" style="margin: 4px 0 0; font-size: 12px">
                目标升级价值：{{
                  upgradeCalcShip.ccu
                    ? moneyFromCents(upgradeCalcShip.ccu.value)
                    : moneyFromCents(calcTargetMaxValueCents)
                }}
              </p>
            </div>
            <button
              type="button"
              class="navbtn btn-icon"
              aria-label="关闭补差价计算弹窗"
              @click="closeUpgradeCalcModal"
            >
              <X :size="15" aria-hidden="true" />
            </button>
          </div>

          <div class="upgrade-calc-body">
            <div class="upgrade-calc-picker">
              <label class="field-label">
                搜索我拥有的可升级船
                <input
                  v-model="calcFromSearch"
                  type="search"
                  class="input"
                  placeholder="输入中文/英文船名或厂商快速过滤…"
                />
              </label>
              <label class="field-label">
                选择你的可升级船
                <select v-model="calcFromKey" class="select">
                  <option value="">请选择我拥有的可升级船…</option>
                  <option v-for="opt in calcEligibleOptions" :key="opt.key" :value="opt.key">
                    {{ opt.label }}
                  </option>
                </select>
              </label>
            </div>

            <p v-if="calcLookupState === 'loading' && calcFromShip" class="lookup" aria-live="polite">
              正在向 RSI 升级商店核实从「{{
                formatShipDisplay(calcFromShip.name_en, calcFromShip.name_zh, appStore.languageMode)
              }}」升级的实时补差价…
            </p>
            <p v-else-if="calcLookupState === 'error' && calcFromShip" class="lookup bad" aria-live="polite">
              RSI 实时接口暂时不可用，当前显示按目标价与原船估值计算的估算差价 (≈)。
              <button
                type="button"
                class="link"
                @click="calcFromRefId && fetchCalcUpgradeCostsForRef(calcFromRefId)"
              >
                重试查询
              </button>
            </p>

            <div class="upgrade-calc-skus">
              <div v-for="up in upgradeCalcShip.upgrades" :key="up.sku" class="upgrade-calc-sku-row">
                <div class="upgrade-calc-sku-meta">
                  <span class="badge" :class="up.is_warbond ? 'badge-wb' : 'badge-muted'">
                    {{ up.is_warbond ? (/bis/i.test(up.label) ? "BIS WBCCU" : "WBCCU") : "标准 CCU" }}
                  </span>
                  <strong>{{ up.label }}</strong>
                  <span class="muted">升级目标价 {{ moneyFromCents(up.price) }}</span>
                  <span
                    v-if="up.is_warbond && upgradeCalcShip.ccu && up.price < upgradeCalcShip.ccu.value"
                    class="upgrade-calc-save"
                  >
                    立省 {{ moneyFromCents(upgradeCalcShip.ccu.value - up.price) }}
                  </span>
                </div>

                <div class="upgrade-calc-cost">
                  <template v-if="!calcFromShip">
                    <span class="muted">请先选择上方的可升级船</span>
                  </template>
                  <template v-else-if="calcEffectiveCosts">
                    <template v-if="String(up.sku) in calcEffectiveCosts">
                      <strong>补差 {{ moneyFromCents(calcEffectiveCosts[String(up.sku)]) }}</strong>
                      <span class="upgrade-calc-verified">RSI 已核实</span>
                    </template>
                    <span v-else class="muted">当前无法从所选可升级船直接购买此 SKU</span>
                  </template>
                  <template v-else>
                    <template v-if="up.price - calcFromPriceCents > 0">
                      <strong>约补 {{ moneyFromCents(up.price - calcFromPriceCents) }}</strong>
                      <span class="muted">估算参考</span>
                    </template>
                    <span v-else class="muted">原船估值不低于该档位目标价</span>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <div class="modal-foot">
            <button
              v-if="plannerEnabled && upgradeCalcShip.ccu"
              type="button"
              class="navbtn"
              @click="openPlannerFromUpgradeCalc"
            >
              规划连跳路线
            </button>
            <span v-else />
            <div class="btn-row">
              <button
                v-if="calcFromKey"
                type="button"
                class="navbtn primary"
                @click="applyCalcFromToStore"
              >
                应用到商店列表
              </button>
              <button type="button" class="navbtn" @click="closeUpgradeCalcModal">关闭</button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>
