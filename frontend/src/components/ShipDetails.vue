<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp, GripVertical } from "lucide-vue-next";
import CargoGridSvg from "./CargoGridSvg.vue";
import {
  appStore,
  formatAuec,
  formatHoursLabel,
  formatShipDisplay,
  moneyFromCents,
} from "../composables/useAppStore";
import type { StoreShip } from "../types/api";

type DetailCardId = "ingame" | "insurance" | "weapons" | "loadouts" | "donors" | "history" | "cargo";

const DETAILS_ORDER_KEY = "ccujailer_details_card_order";
const DETAILS_COLLAPSED_KEY = "ccujailer_details_collapsed_cards";
const DEFAULT_CARD_ORDER: DetailCardId[] = [
  "ingame",
  "insurance",
  "weapons",
  "loadouts",
  "donors",
  "history",
  "cargo",
];
const PAIRABLE_CARDS = new Set<DetailCardId>(["ingame", "insurance"]);
const CARD_TITLES: Record<DetailCardId, string> = {
  ingame: "游戏内获取",
  insurance: "保险与借用船",
  weapons: "原厂武器配置",
  loadouts: "配装模拟 (Erkul)",
  donors: "最近可升级起始底船推荐 (CCU)",
  history: "历史商店售价记录",
  cargo: "俯视货舱网格",
};

function loadDetailCardOrder(): DetailCardId[] {
  try {
    const raw = localStorage.getItem(DETAILS_ORDER_KEY);
    if (!raw) {
      return [...DEFAULT_CARD_ORDER];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [...DEFAULT_CARD_ORDER];
    }
    const valid = parsed.filter(
      (id): id is DetailCardId =>
        typeof id === "string" && DEFAULT_CARD_ORDER.includes(id as DetailCardId)
    );
    const seen = new Set(valid);
    for (const def of DEFAULT_CARD_ORDER) {
      if (!seen.has(def)) {
        valid.push(def);
      }
    }
    return valid;
  } catch {
    return [...DEFAULT_CARD_ORDER];
  }
}

function loadCollapsedCards(): Set<DetailCardId> {
  try {
    const raw = localStorage.getItem(DETAILS_COLLAPSED_KEY);
    if (!raw) {
      return new Set();
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(
      parsed.filter(
        (id): id is DetailCardId =>
          typeof id === "string" && DEFAULT_CARD_ORDER.includes(id as DetailCardId)
      )
    );
  } catch {
    return new Set();
  }
}

function saveDetailStorage(key: string, val: DetailCardId[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(val));
  } catch {
    // 忽略本地存储写入失败
  }
}

const props = defineProps<{
  ship: StoreShip;
  rate: number;
  gameVersion: string;
}>();

const rentOpen = ref(false);
const showAllHistory = ref(false);
const cardOrder = ref<DetailCardId[]>(loadDetailCardOrder());
const collapsedCards = ref<Set<DetailCardId>>(loadCollapsedCards());
const draggingCardId = ref<DetailCardId | null>(null);
const dragOverCardId = ref<DetailCardId | null>(null);

const MOUNT_LABELS: Record<string, string> = {
  hardpoints: "固定挂载点",
  manned: "载人炮塔",
  remote: "遥控炮塔",
  pdc: "点防御炮塔",
  "manned-missile": "载人导弹炮塔",
  "remote-missile": "遥控导弹炮塔",
};

const WHO_GROUPS: [string, string][] = [
  ["pilot", "主驾直控"],
  ["both", "主驾可接管 / 炮手"],
  ["gunner", "炮手操控"],
  ["auto", "自动点防御"],
];

const MISSILE_LABELS: Record<string, string> = {
  missile: "导弹",
  torpedo: "鱼雷",
  bomb: "炸弹",
  rocket: "火箭弹",
};

const SOLD_DESC: Record<string, string> = {
  time: "仅在限时大型活动期间短暂返场销售",
  quantity: "仅在限时定量抢购波次中放出库存",
  retiring: "官方已宣布或即将从公开商店退市",
  always: "通常在公开单船商店长期开放直购",
};

function lowestPrice(list: [number, string][]): number {
  return list.length ? Math.min(...list.map((row) => row[0])) : 0;
}

const buyMin = computed(() => lowestPrice(props.ship.buy));
const buyPlace = computed(
  () => props.ship.buy.find((row) => row[0] === buyMin.value)?.[1] || ""
);
const rentMin = computed(() => lowestPrice(props.ship.rent));
const rentPlace = computed(
  () => props.ship.rent.find((row) => row[0] === rentMin.value)?.[1] || ""
);

const rentGrouped = computed(() => {
  const byPrice: Record<string, string[]> = {};
  const sorted = [...props.ship.rent].sort((a, b) => a[0] - b[0]);
  for (const [price, place] of sorted) {
    const k = formatAuec(price);
    (byPrice[k] ||= []).push(place);
  }
  return Object.entries(byPrice).map(([priceText, places]) => ({
    priceText,
    places: [...places].sort(),
  }));
});

const pledgeDollars = computed(() => {
  const inStoreEditions = props.ship.editions.filter((e) => e.in_store && e.price > 0);
  if (inStoreEditions.length) {
    return Math.min(...inStoreEditions.map((e) => e.price)) / 100;
  }
  if (props.ship.ccu?.value) {
    return props.ship.ccu.value / 100;
  }
  const h = props.ship.history || [];
  return h.length ? h[h.length - 1][1] : 0;
});

const valuePerDollar = computed(() =>
  buyMin.value && pledgeDollars.value ? Math.round(buyMin.value / pledgeDollars.value) : 0
);

const storeInsuranceText = computed(() => {
  const insList = props.ship.editions
    .filter((e) => e.in_store && e.insurance)
    .map((e) => `${e.label}: ${e.insurance === "Lifetime" ? "LTI 终身保险" : e.insurance}`);
  return [...new Set(insList)].join(" · ");
});

const goldText = computed(() => {
  const g = props.ship.gold;
  if (!g) {
    return "";
  }
  const fam = g.family ? `（${g.family} 系列）` : "";
  return g.status === "done"
    ? `已完成金牌标准（Gold Standard）重制${fam}${g.patch ? ` · 实装于 ${g.patch}` : ""}`
    : `金牌标准（Gold Standard）重制进行中${fam}`;
});

const weaponsTotalAll = computed(() => {
  const guns = props.ship.weapons?.guns || [];
  return ([4, 5, 6] as const).map((col) =>
    guns.reduce((sum, row) => sum + Number(row[col] || 0), 0)
  );
});

const hasCrewedGuns = computed(() =>
  Boolean(props.ship.weapons?.guns.some((r) => (r[0] === "gunner" || r[0] === "auto") && r[4]))
);

const hasBeamWeapon = computed(() =>
  Boolean(props.ship.weapons?.guns.some((r) => r[4] > 0 && !r[5] && !r[6]))
);

function formatMount(mount: string, count: number): string {
  const label = MOUNT_LABELS[mount] || mount;
  return mount === "hardpoints" ? label : `${count}× ${label}`;
}

function formatNum(val: number): string {
  return val ? val.toLocaleString("en-US") : "–";
}

const historyRowsDesc = computed(() => [...(props.ship.history || [])].reverse());
const shownHistory = computed(() =>
  showAllHistory.value ? historyRowsDesc.value : historyRowsDesc.value.slice(0, 8)
);

const lowestWbHistory = computed(() => {
  const wb = (props.ship.history || []).filter((row) => row[2]);
  return wb.length ? wb.reduce((a, b) => (b[1] < a[1] ? b : a)) : null;
});

const lastWbHistory = computed(() => {
  const wb = (props.ship.history || []).filter((row) => row[2]);
  return wb.length ? wb[wb.length - 1] : null;
});

const lastStdHistory = computed(() => {
  const std = (props.ship.history || []).filter((row) => !row[2] && row[3] === "store");
  return std.length ? std[std.length - 1] : null;
});

function formatMonth(dateStr: string): string {
  if (!dateStr) {
    return "-";
  }
  const d = new Date(`${dateStr}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) {
    return dateStr;
  }
  return d.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

const erkulShipUrl = computed(
  () => `https://erkul.games/ship/${encodeURIComponent(props.ship.class_name)}`
);
const erkulBuildsUrl = computed(
  () => `https://erkul.games/browse?q=${encodeURIComponent(props.ship.name_en)}`
);
const wikiSearchUrl = computed(
  () => `https://starcitizen.tools/index.php?search=${encodeURIComponent(props.ship.name_en)}`
);

const upgradeDonors = computed(() =>
  props.ship.upgrades.filter((u) => (u.nearest_from_ships?.length || 0) > 0)
);

const hasCargoGrid = computed(() =>
  Boolean(props.ship.grid || Object.keys(props.ship.module_grids || {}).length)
);

function isCardVisible(cardId: DetailCardId): boolean {
  if (cardId === "weapons") {
    return Boolean(props.ship.weapons);
  }
  if (cardId === "donors") {
    return upgradeDonors.value.length > 0;
  }
  if (cardId === "history") {
    return props.ship.history.length > 0;
  }
  if (cardId === "cargo") {
    return hasCargoGrid.value;
  }
  return true;
}

const orderedVisibleCards = computed<DetailCardId[]>(() => cardOrder.value.filter(isCardVisible));

const wideCardSet = computed<Set<DetailCardId>>(() => {
  const visible = orderedVisibleCards.value;
  const pairedIndices = new Set<number>();
  for (let i = 0; i < visible.length - 1; i += 1) {
    if (PAIRABLE_CARDS.has(visible[i]) && PAIRABLE_CARDS.has(visible[i + 1])) {
      pairedIndices.add(i);
      pairedIndices.add(i + 1);
      i += 1;
    }
  }
  const wide = new Set<DetailCardId>();
  visible.forEach((id, idx) => {
    if (!PAIRABLE_CARDS.has(id) || !pairedIndices.has(idx)) {
      wide.add(id);
    }
  });
  return wide;
});

const hasCustomCardOrder = computed(() =>
  cardOrder.value.some((id, idx) => id !== DEFAULT_CARD_ORDER[idx])
);

const allCardsCollapsed = computed(
  () =>
    orderedVisibleCards.value.length > 0 &&
    orderedVisibleCards.value.every((id) => collapsedCards.value.has(id))
);

function toggleCardCollapse(cardId: DetailCardId): void {
  const next = new Set(collapsedCards.value);
  if (next.has(cardId)) {
    next.delete(cardId);
  } else {
    next.add(cardId);
  }
  collapsedCards.value = next;
  saveDetailStorage(DETAILS_COLLAPSED_KEY, [...next]);
}

function toggleCollapseAllCards(): void {
  const next = new Set(collapsedCards.value);
  if (allCardsCollapsed.value) {
    for (const id of orderedVisibleCards.value) {
      next.delete(id);
    }
  } else {
    for (const id of orderedVisibleCards.value) {
      next.add(id);
    }
  }
  collapsedCards.value = next;
  saveDetailStorage(DETAILS_COLLAPSED_KEY, [...next]);
}

function applyCardReorder(sourceId: DetailCardId, targetVisibleIdx: number): void {
  const visible = orderedVisibleCards.value.slice();
  const fromVisibleIdx = visible.indexOf(sourceId);
  if (
    fromVisibleIdx === -1 ||
    targetVisibleIdx < 0 ||
    targetVisibleIdx >= visible.length ||
    fromVisibleIdx === targetVisibleIdx
  ) {
    return;
  }
  const [moved] = visible.splice(fromVisibleIdx, 1);
  visible.splice(targetVisibleIdx, 0, moved);

  const visibleSet = new Set(visible);
  const hiddenTail = cardOrder.value.filter((id) => !visibleSet.has(id));
  const merged = [...visible, ...hiddenTail];
  cardOrder.value = merged;
  saveDetailStorage(DETAILS_ORDER_KEY, merged);
}

function moveCard(cardId: DetailCardId, direction: -1 | 1): void {
  const idx = orderedVisibleCards.value.indexOf(cardId);
  if (idx !== -1) {
    applyCardReorder(cardId, idx + direction);
  }
}

function resetCardOrder(): void {
  cardOrder.value = [...DEFAULT_CARD_ORDER];
  saveDetailStorage(DETAILS_ORDER_KEY, DEFAULT_CARD_ORDER);
}

function handleCardDragStart(cardId: DetailCardId, event: DragEvent): void {
  draggingCardId.value = cardId;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", cardId);
  }
}

function handleCardDragOver(cardId: DetailCardId): void {
  if (draggingCardId.value && draggingCardId.value !== cardId) {
    dragOverCardId.value = cardId;
  }
}

function handleCardDrop(targetId: DetailCardId, event: DragEvent): void {
  const raw = draggingCardId.value || event.dataTransfer?.getData("text/plain") || "";
  if (!DEFAULT_CARD_ORDER.includes(raw as DetailCardId)) {
    draggingCardId.value = null;
    dragOverCardId.value = null;
    return;
  }
  const targetIdx = orderedVisibleCards.value.indexOf(targetId);
  if (targetIdx !== -1) {
    applyCardReorder(raw as DetailCardId, targetIdx);
  }
  draggingCardId.value = null;
  dragOverCardId.value = null;
}

function handleCardDragEnd(): void {
  draggingCardId.value = null;
  dragOverCardId.value = null;
}
</script>

<template>
  <div class="details">
    <div class="details-toolbar">
      <span class="details-toolbar-hint">卡片支持折叠与拖曳/上下调整顺序（自动保存偏好）</span>
      <div class="details-toolbar-actions">
        <button type="button" class="link" @click="toggleCollapseAllCards">
          {{ allCardsCollapsed ? "全部展开" : "全部折叠" }}
        </button>
        <button v-if="hasCustomCardOrder" type="button" class="link" @click="resetCardOrder">
          恢复默认顺序
        </button>
      </div>
    </div>

    <section
      v-for="(cardId, idx) in orderedVisibleCards"
      :key="cardId"
      class="detail-card"
      :data-card-id="cardId"
      :class="{
        wide: wideCardSet.has(cardId),
        loadouts: cardId === 'loadouts',
        collapsed: collapsedCards.has(cardId),
        'is-dragging': draggingCardId === cardId,
        'is-drag-over': dragOverCardId === cardId,
      }"
      draggable="true"
      @dragstart="handleCardDragStart(cardId, $event)"
      @dragover.prevent="handleCardDragOver(cardId)"
      @drop.prevent="handleCardDrop(cardId, $event)"
      @dragend="handleCardDragEnd"
    >
      <div class="detail-card-head">
        <h3>{{ CARD_TITLES[cardId] }}</h3>
        <div class="card-controls">
          <span class="card-tool-btn drag-handle" title="拖曳调整卡片顺序" aria-hidden="true">
            <GripVertical :size="12" aria-hidden="true" />
          </span>
          <button
            type="button"
            class="card-tool-btn"
            aria-label="上移卡片"
            title="上移"
            :disabled="idx === 0"
            @click="moveCard(cardId, -1)"
          >
            <ArrowUp :size="12" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="card-tool-btn"
            aria-label="下移卡片"
            title="下移"
            :disabled="idx === orderedVisibleCards.length - 1"
            @click="moveCard(cardId, 1)"
          >
            <ArrowDown :size="12" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="card-tool-btn"
            :aria-expanded="!collapsedCards.has(cardId)"
            :aria-label="collapsedCards.has(cardId) ? `展开${CARD_TITLES[cardId]}` : `折叠${CARD_TITLES[cardId]}`"
            @click="toggleCardCollapse(cardId)"
          >
            <ChevronDown v-if="collapsedCards.has(cardId)" :size="12" aria-hidden="true" />
            <ChevronUp v-else :size="12" aria-hidden="true" />
            <span>{{ collapsedCards.has(cardId) ? "展开" : "折叠" }}</span>
          </button>
        </div>
      </div>

      <div v-if="!collapsedCards.has(cardId)" class="detail-card-body">
        <!-- 1. 游戏内购买/租赁 -->
        <template v-if="cardId === 'ingame'">
          <p
            v-if="ship.gold"
            class="gold-note"
            :class="{ done: ship.gold.status === 'done' }"
          >
            {{ goldText }}
          </p>

          <dl v-if="buyMin" class="facts">
            <dt>购买价格</dt>
            <dd><b>{{ formatAuec(buyMin) }}</b> · {{ buyPlace }}</dd>
            <dt>预计肝度</dt>
            <dd>
              约 <b>{{ formatHoursLabel(buyMin / rate) }}</b>（按每小时 {{ formatAuec(rate) }} 计算）
            </dd>
            <template v-if="pledgeDollars">
              <dt>aUEC 性价比</dt>
              <dd>
                {{ valuePerDollar.toLocaleString("en-US") }} aUEC / $（折算 ${{ pledgeDollars.toLocaleString("en-US") }}）
              </dd>
            </template>
            <template v-if="rentMin">
              <dt>租赁价格</dt>
              <dd>
                <b>{{ formatAuec(rentMin) }}</b> / 天 · {{ rentPlace }}
                <template v-if="ship.rent.length > 1">
                  ·
                  <button
                    type="button"
                    class="link"
                    :aria-expanded="rentOpen"
                    @click="rentOpen = !rentOpen"
                  >
                    {{ rentOpen ? "收起地点" : `查看全部 ${ship.rent.length} 个租赁点` }}
                  </button>
                </template>
              </dd>
              <dd v-if="rentOpen" class="places">
                <ul>
                  <li v-for="grp in rentGrouped" :key="grp.priceText">
                    <b>{{ grp.priceText }}</b>
                    <template v-if="grp.places.length > 1">（共 {{ grp.places.length }} 处）</template>：
                    {{ grp.places.join("、") }}
                  </li>
                </ul>
              </dd>
            </template>
          </dl>

          <dl v-else-if="rentMin" class="facts">
            <dt>游戏内直购</dt>
            <dd class="muted">当前版本暂未开放游戏内直接购买</dd>
            <dt>租赁价格</dt>
            <dd>
              <b>{{ formatAuec(rentMin) }}</b> / 天 · {{ rentPlace }}
              <template v-if="ship.rent.length > 1">
                ·
                <button
                  type="button"
                  class="link"
                  :aria-expanded="rentOpen"
                  @click="rentOpen = !rentOpen"
                >
                  {{ rentOpen ? "收起地点" : `查看全部 ${ship.rent.length} 个租赁点` }}
                </button>
              </template>
            </dd>
            <dd v-if="rentOpen" class="places">
              <ul>
                <li v-for="grp in rentGrouped" :key="grp.priceText">
                  <b>{{ grp.priceText }}</b>：{{ grp.places.join("、") }}
                </li>
              </ul>
            </dd>
          </dl>

          <p v-else class="muted">
            当前版本暂未开放游戏内商店购买或租赁。通常全新舰船会优先登陆网页商店。
          </p>

          <ul v-if="ship.buy.length > 1" class="where">
            <li v-for="[price, terminal] in ship.buy" :key="`${terminal}-${price}`">
              <span>{{ terminal }}</span>
              <span>{{ formatAuec(price) }}</span>
            </li>
          </ul>
        </template>

        <!-- 2. 保险与借用船 -->
        <template v-else-if="cardId === 'insurance'">
          <dl class="facts">
            <template v-if="ship.insurance">
              <dt>常规索赔</dt>
              <dd><b>{{ ship.insurance.claim }} 分钟</b></dd>
              <dt>加急取船</dt>
              <dd>
                <b>{{ ship.insurance.expedite }} 分钟</b>（加急费
                {{ ship.insurance.expedite_cost.toLocaleString("en-US") }} aUEC）
              </dd>
            </template>
            <template v-else>
              <dt>索赔时间</dt>
              <dd class="muted">暂无数据（未实装或暂无游戏内配置）</dd>
            </template>

            <template v-if="storeInsuranceText">
              <dt>商店保险</dt>
              <dd>{{ storeInsuranceText }}</dd>
            </template>
            <template v-if="ship.loaners">
              <dt>借用替代船</dt>
              <dd>{{ ship.loaners }}</dd>
            </template>
          </dl>
          <p v-if="gameVersion && ship.insurance" class="muted small">
            索赔数据取自游戏版本 {{ gameVersion }}。
          </p>
        </template>

        <!-- 3. 原厂武器与炮塔配置 -->
        <template v-else-if="cardId === 'weapons' && ship.weapons">
          <div v-if="ship.weapons.guns.length" class="table-wrap">
            <table class="loadout">
              <thead>
                <tr>
                  <th class="mount">位置</th>
                  <th>武器配置</th>
                  <th class="num">峰值 DPS</th>
                  <th class="num">持续 DPS</th>
                  <th class="num">单轮齐射</th>
                </tr>
              </thead>
              <template v-for="[whoKey, whoLabel] in WHO_GROUPS" :key="whoKey">
                <tbody v-if="ship.weapons.guns.some((r) => r[0] === whoKey)">
                  <tr class="group">
                    <th colspan="5" scope="rowgroup">{{ whoLabel }}</th>
                  </tr>
                  <tr
                    v-for="([, mount, count, each, dps, sus, alpha], rowIdx) in ship.weapons.guns.filter(
                      (r) => r[0] === whoKey
                    )"
                    :key="`${whoKey}-${rowIdx}`"
                  >
                    <td class="mount">{{ formatMount(mount, count) }}</td>
                    <td class="guns">
                      <span class="mount-inline">{{ formatMount(mount, count) }}</span>
                      <div v-for="([cnt, sz, gunName], gIdx) in each" :key="gIdx">
                        <b>{{ cnt }}×</b> {{ sz ? `S${sz} ` : "" }}{{ gunName }}
                      </div>
                      <span v-if="count > 1 && mount !== 'hardpoints'" class="each">每座炮塔</span>
                      <span v-if="dps" class="stats-inline">
                        <b>{{ formatNum(dps) }}</b> 峰值 · <b>{{ formatNum(sus) }}</b> 持续 ·
                        <b>{{ formatNum(alpha) }}</b> 齐射
                      </span>
                    </td>
                    <td class="num">{{ formatNum(dps) }}</td>
                    <td class="num">{{ formatNum(sus) }}</td>
                    <td class="num">{{ formatNum(alpha) }}</td>
                  </tr>
                </tbody>
              </template>
              <tfoot>
                <tr>
                  <th colspan="2" scope="row">
                    仅主驾火力
                    <span v-if="ship.weapons.pilot[0]" class="stats-inline">
                      <b>{{ formatNum(ship.weapons.pilot[0]) }}</b> 峰值 ·
                      <b>{{ formatNum(ship.weapons.pilot[1]) }}</b> 持续 ·
                      <b>{{ formatNum(ship.weapons.pilot[2]) }}</b> 齐射
                    </span>
                  </th>
                  <td class="num">{{ formatNum(ship.weapons.pilot[0]) }}</td>
                  <td class="num">{{ formatNum(ship.weapons.pilot[1]) }}</td>
                  <td class="num">{{ formatNum(ship.weapons.pilot[2]) }}</td>
                </tr>
                <tr v-if="hasCrewedGuns">
                  <th colspan="2" scope="row">
                    满员全炮塔火力
                    <span v-if="weaponsTotalAll[0]" class="stats-inline">
                      <b>{{ formatNum(weaponsTotalAll[0]) }}</b> 峰值 ·
                      <b>{{ formatNum(weaponsTotalAll[1]) }}</b> 持续 ·
                      <b>{{ formatNum(weaponsTotalAll[2]) }}</b> 齐射
                    </span>
                  </th>
                  <td class="num">{{ formatNum(weaponsTotalAll[0]) }}</td>
                  <td class="num">{{ formatNum(weaponsTotalAll[1]) }}</td>
                  <td class="num">{{ formatNum(weaponsTotalAll[2]) }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p v-else class="muted">原厂未配备固定火炮或炮塔武器。</p>

          <dl v-if="ship.weapons.missiles.length" class="facts arms-facts">
            <dt>挂载导弹/雷弹</dt>
            <dd>
              {{
                ship.weapons.missiles
                  .map(([cnt, sz, kind]) => `${cnt}× ${sz ? `S${sz} ` : ""}${MISSILE_LABELS[kind] || kind}`)
                  .join(" · ")
              }}
            </dd>
          </dl>

          <p class="muted small">
            <b>峰值 DPS</b> 为开火瞬间秒伤，<b>持续 DPS</b> 计入电容充能与散热上限，<b>单轮齐射</b> 为全部武器单次打击总伤（数据源自 Star Citizen Wiki{{ gameVersion ? ` ${gameVersion}` : "" }}）。
            <template v-if="hasBeamWeapon">光束武器为连续输出，无单发齐射概念。</template>
          </p>
        </template>

        <!-- 4. Erkul 第三方配装器 -->
        <template v-else-if="cardId === 'loadouts'">
          <div class="lo">
            <div v-if="ship.class_name">
              <a class="navbtn" :href="erkulShipUrl" target="_blank" rel="noopener">
                打开原厂配置计算器 ↗
              </a>
              <p>前往 erkul.games 调整 {{ ship.name_en }} 的武器、护盾、量子引擎与冷却器。</p>
            </div>
            <div>
              <a class="navbtn" :href="erkulBuildsUrl" target="_blank" rel="noopener">
                查看玩家热门配装 ↗
              </a>
              <p>浏览社区玩家分享并点赞最多的 {{ ship.name_en }} 实战配装方案。</p>
            </div>
          </div>
        </template>

        <!-- 5. 最近可升级起始底船（CCU 起跳推荐） -->
        <template v-else-if="cardId === 'donors'">
          <div class="stack-md">
            <div v-for="up in upgradeDonors" :key="up.sku" class="stack-sm">
              <div class="row-between">
                <strong>
                  {{ up.label }} · {{ moneyFromCents(up.price) }}
                  <span v-if="up.is_warbond" class="pill warn pill-inline">Warbond</span>
                </strong>
                <span class="muted small">推荐从下方最接近价值底船升级至此 SKU</span>
              </div>
              <div class="table-wrap">
                <table class="hist">
                  <thead>
                    <tr>
                      <th>起始底船</th>
                      <th class="num">当前估值</th>
                      <th class="num">需补差价</th>
                      <th>商店属性</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="cand in up.nearest_from_ships || []" :key="cand.name_en">
                      <td>{{ formatShipDisplay(cand.name_en, cand.name_zh, appStore.languageMode) }}</td>
                      <td class="num">${{ cand.value.toLocaleString("en-US") }}</td>
                      <td class="num"><b>+${{ cand.upgrade_cost.toLocaleString("en-US") }}</b></td>
                      <td>
                        <span v-if="cand.always_available" class="pill good">常驻直购</span>
                        <span v-else class="pill neutral">限时返场</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </template>

        <!-- 6. 历史价格轨迹 -->
        <template v-else-if="cardId === 'history'">
          <p v-if="ship.sold && SOLD_DESC[ship.sold]" class="muted small sold-note">
            售卖规律说明：{{ SOLD_DESC[ship.sold] }}（据 Star Citizen Wiki 玩家记录，仅供参考）。
          </p>
          <div class="stats">
            <div v-if="lastStdHistory">
              <span>常规记录价</span>
              <b>${{ lastStdHistory[1].toLocaleString("en-US") }}</b>
            </div>
            <div>
              <span>历史最低 Warbond</span>
              <b>{{ lowestWbHistory ? `$${lowestWbHistory[1].toLocaleString("en-US")}` : "暂无记录" }}</b>
              <i v-if="lowestWbHistory">{{ formatMonth(lowestWbHistory[0]) }}</i>
            </div>
            <div v-if="lastWbHistory">
              <span>最近一次 Warbond</span>
              <b>${{ lastWbHistory[1].toLocaleString("en-US") }}</b>
              <i>{{ formatMonth(lastWbHistory[0]) }}</i>
            </div>
          </div>

          <div class="table-wrap">
            <table class="hist">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>版本</th>
                  <th>渠道</th>
                  <th class="num">价格</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="([dateStr, usd, isWb, src], rowIdx) in shownHistory" :key="rowIdx">
                  <td>{{ formatMonth(dateStr) }}</td>
                  <td :class="{ wb: isWb }">{{ isWb ? "Warbond" : "Standard" }}</td>
                  <td class="muted">{{ src === "upgrade" ? "升级商店 (CCU)" : "单船商店" }}</td>
                  <td class="num">${{ usd.toLocaleString("en-US") }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <button
            v-if="historyRowsDesc.length > 8"
            type="button"
            class="link section-action-link"
            @click="showAllHistory = !showAllHistory"
          >
            {{ showAllHistory ? "收起历史记录" : `展开全部 ${historyRowsDesc.length} 条记录` }}
          </button>
          <p class="muted small">升级商店记录为该船作为 CCU 目标的机身估值，可能与单船购买折扣价不同。</p>
        </template>

        <!-- 7. 俯视货舱网格 -->
        <CargoGridSvg v-else-if="cardId === 'cargo'" :ship="ship" />
      </div>
    </section>

    <p class="links">
      <a v-if="ship.rsi" :href="ship.rsi" target="_blank" rel="noopener">RSI 官方页面 ↗</a>
      <a :href="wikiSearchUrl" target="_blank" rel="noopener">Star Citizen Wiki ↗</a>
      <a v-if="ship.grid" :href="ship.grid.viewer" target="_blank" rel="noopener">3D 货舱模型 ↗</a>
    </p>
  </div>
</template>
