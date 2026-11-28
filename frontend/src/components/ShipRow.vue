<script setup lang="ts">
import { computed, ref } from "vue";
import {
  appStore,
  formatAgo,
  formatAuec,
  formatHoursLabel,
  formatShipDisplay,
  moneyFromCents,
} from "../composables/useAppStore";
import type { StoreEdition, StoreShip } from "../types/api";
import {
  formatManufacturer,
  formatShipRoleZh,
  formatShipSizeZh,
} from "../utils/localization";

const props = defineProps<{
  ship: StoreShip;
  rate: number;
  fromKey: string;
  fromName: string;
  fromPriceCents: number;
  costsMap: Record<string, number> | null;
  isEstimate: boolean;
  gameVersion: string;
  byValueSort: boolean;
  plannerEnabled: boolean;
  staggerIndex?: number;
}>();

const emit = defineEmits<{
  (e: "pickFromShip", ship: StoreShip): void;
  (e: "planRoute", payload: { fromKey: string; toRef: number }): void;
  (e: "openDetails", ship: StoreShip): void;
}>();

const explainWhy = ref(false);

const CCU_TOOL_URL = "https://robertsspaceindustries.com/en/account/pledges";

const SOLD_TAGS: Record<string, [string, string]> = {
  time: ["限时开售", "仅在周年或厂商活动等限时促销期间开放购买"],
  quantity: ["限量发售", "仅在特定时间限量分批抢购"],
  retiring: ["即将退市", "官方即将从公开商店下架该条目"],
};

const displayTitle = computed(() =>
  formatShipDisplay(props.ship.name_en, props.ship.name_zh, appStore.languageMode)
);

const anchorId = computed(() => `ship-${props.ship.key.replace(/\s+/g, "-")}`);

const specsLine = computed(() => {
  const s = props.ship;
  const parts = [
    formatManufacturer(s.maker, s.maker_code, appStore.languageMode),
    formatShipRoleZh(s.role),
    formatShipSizeZh(s.size),
    s.crew ? `乘员 ${s.crew}` : "",
    s.cargo ? `${s.cargo.toLocaleString("en-US")} SCU` : "",
    s.released ? (s.released === "0" ? "早期机库已可飞" : `${s.released} 已可飞`) : "",
  ];
  return parts.filter(Boolean).join(" · ");
});

const soldTag = computed(() => SOLD_TAGS[props.ship.sold] || null);
const notFlyable = computed(
  () => Boolean(props.ship.status && props.ship.status !== "Flight ready")
);

const statusChinese = computed(() => {
  if (props.ship.status === "In concept") {
    return "画饼中";
  }
  if (props.ship.status === "In production") {
    return "正在开发中";
  }
  return props.ship.status;
});

const armsSummaryLine = computed(() => {
  const w = props.ship.weapons;
  if (!w) {
    return "";
  }
  const own = w.guns.find((r) => r[0] === "pilot");
  const sizes: Record<string, number> = {};
  if (own) {
    for (const [cnt, sz] of own[3]) {
      const k = sz ? String(sz) : "0";
      sizes[k] = (sizes[k] || 0) + cnt;
    }
  }
  const gunsText = Object.entries(sizes)
    .sort((a, b) => Number(b[0]) - Number(a[0]))
    .map(([sz, cnt]) => (sz === "0" ? `${cnt}× 武器` : `${cnt}× S${sz}`))
    .join(", ");
  const countWho = (who: string) =>
    w.guns.filter((r) => r[0] === who).reduce((sum, r) => sum + r[2], 0);
  const slaveTurrets = countWho("both");
  const pilotText = [gunsText, slaveTurrets ? `${slaveTurrets} 炮塔` : ""]
    .filter(Boolean)
    .join(" + ");
  const gunners = countWho("gunner");
  const pdc = countWho("auto");
  return [
    `主驾: ${pilotText || "无直控武器"}`,
    gunners ? `炮手: ${gunners} 炮塔` : "",
    pdc ? `${pdc} PDC` : "",
  ]
    .filter(Boolean)
    .join(" · ");
});

const onSaleEditions = computed(() => props.ship.editions.filter((e) => e.in_store));
const editionCards = computed(() => {
  const plain = onSaleEditions.value.filter((e) => !e.modules.length);
  return plain.length ? plain : onSaleEditions.value;
});
const modulePacks = computed(() => {
  const plain = onSaleEditions.value.filter((e) => !e.modules.length);
  return plain.length ? onSaleEditions.value.filter((e) => e.modules.length) : [];
});

const standardEdition = computed(() =>
  props.ship.editions.find(
    (e) => e.in_store && e.label === "Standard" && !e.modules.length
  )
);

const estimateUpgradeDiff = computed(() => {
  if (
    !props.isEstimate ||
    !props.fromKey ||
    props.fromKey === props.ship.key ||
    !standardEdition.value ||
    !props.fromPriceCents
  ) {
    return 0;
  }
  return standardEdition.value.price > props.fromPriceCents
    ? standardEdition.value.price - props.fromPriceCents
    : 0;
});

function editionCssClass(e: StoreEdition): string {
  const slug = e.label.replace(/\s+/g, "").toLowerCase();
  return `ed ed-${slug}${e.available ? "" : " soldout"}`;
}

function formatEditionLabel(e: StoreEdition): string {
  if (e.label === "Warbond") {
    return "WARBOND 整船";
  }
  return e.label;
}

function editionSaving(e: StoreEdition): number {
  if (e.label === "Standard" || !standardEdition.value) {
    return 0;
  }
  return e.price < standardEdition.value.price ? standardEdition.value.price - e.price : 0;
}

function formatInsurance(ins: string): string {
  if (!ins) {
    return "";
  }
  if (ins === "Lifetime") {
    return "LTI 终身保险";
  }
  return ins.replace(" years", " 年保险").replace(" months", " 个月保险");
}

function editionState(e: StoreEdition): string {
  if (!e.available) {
    return "已售罄";
  }
  if (e.limited) {
    return e.level ? `限量 · ${e.level}` : "限量版";
  }
  return "";
}

const buyMin = computed(() =>
  props.ship.buy.length ? Math.min(...props.ship.buy.map((b) => b[0])) : 0
);
const rentMin = computed(() =>
  props.ship.rent.length ? Math.min(...props.ship.rent.map((r) => r[0])) : 0
);

const valuePerDollarText = computed(() => {
  if (!props.byValueSort || !buyMin.value) {
    return "";
  }
  const inStore = props.ship.editions.filter((e) => e.in_store && e.price > 0);
  const usd =
    (inStore.length ? Math.min(...inStore.map((e) => e.price)) : 0) / 100 ||
    (props.ship.history.length ? props.ship.history[props.ship.history.length - 1][1] : 0);
  if (!usd) {
    return "";
  }
  return `${Math.round(buyMin.value / usd).toLocaleString("en-US")} aUEC/$`;
});

const lastSeenIso = computed(() => {
  const timestamps = props.ship.editions
    .map((e) => e.last_seen)
    .filter((v): v is string => Boolean(v));
  return timestamps.sort().pop() || null;
});

const matchingFromUpgrades = computed(() => {
  if (!props.fromKey || !props.costsMap) {
    return [];
  }
  return props.ship.upgrades
    .filter((u) => String(u.sku) in props.costsMap!)
    .sort((a, b) => props.costsMap![String(a.sku)] - props.costsMap![String(b.sku)]);
});

const lowExampleUpgrade = computed(
  () => props.ship.upgrades.find((u) => !/upgrade$/i.test(u.label)) || props.ship.upgrades[0]
);

const warbondUpgradeCards = computed(() =>
  (props.ship.upgrades || []).filter((u) => Boolean(u.is_warbond))
);

interface NearestUpgradeRow {
  key: string;
  tagLabel: string;
  isWarbond: boolean;
  candidates: NonNullable<(typeof props.ship.upgrades)[number]["nearest_from_ships"]>;
}

const nearestUpgradeRows = computed<NearestUpgradeRow[]>(() => {
  const pool = (props.ship.upgrades || []).filter(
    (u) => (u.nearest_from_ships?.length || 0) > 0
  );
  if (!pool.length) {
    return [];
  }
  const picked: { key: string; tagLabel: string; isWarbond: boolean; list: NearestUpgradeRow["candidates"] }[] = [];

  const wbUpgrade = pool.find((u) => Boolean(u.is_warbond));
  if (wbUpgrade?.nearest_from_ships?.length) {
    picked.push({
      key: `wb-${wbUpgrade.sku}`,
      tagLabel: /bis/i.test(wbUpgrade.label) ? "BIS WBCCU" : "WBCCU",
      isWarbond: true,
      list: wbUpgrade.nearest_from_ships,
    });
  }

  const stdCandidates = pool
    .filter((u) => !u.is_warbond)
    .slice()
    .sort((a, b) => a.price - b.price);
  const stdUpgrade = stdCandidates[0];
  if (stdUpgrade?.nearest_from_ships?.length) {
    picked.push({
      key: `std-${stdUpgrade.sku}`,
      tagLabel: "普通 CCU",
      isWarbond: false,
      list: stdUpgrade.nearest_from_ships,
    });
  }

  return picked
    .map((entry) => {
      const minCost = Math.min(...entry.list.map((cand) => cand.upgrade_cost));
      const candidates = entry.list.filter(
        (cand) => Math.abs(cand.upgrade_cost - minCost) < 0.005
      );
      return {
        key: entry.key,
        tagLabel: entry.tagLabel,
        isWarbond: entry.isWarbond,
        candidates,
      };
    })
    .filter((entry) => entry.candidates.length > 0);
});
</script>

<template>
  <li
    :id="anchorId"
    class="ship ship-reveal"
    :class="{ off: !ship.in_store }"
    :style="{ '--stagger-idx': staggerIndex ?? 0 }"
  >
    <img
      v-if="ship.img"
      :src="ship.img"
      :alt="ship.name_en"
      width="160"
      height="90"
      loading="lazy"
      decoding="async"
    />
    <div v-else class="noimg" translate="no">{{ ship.maker_code || ship.maker || "SHIP" }}</div>

    <!-- 左侧主体：舰船中英标题、物理规格、售卖标签/武器行、详情弹出开关 -->
    <div class="info">
      <h2>
        <a v-if="ship.rsi" :href="ship.rsi" target="_blank" rel="noopener">{{ displayTitle }}</a>
        <span v-else>{{ displayTitle }}</span>
        <a class="here" :href="`#${anchorId}`" aria-label="定位到此舰船">#</a>
      </h2>

      <p v-if="specsLine" class="specs">{{ specsLine }}</p>

      <p v-if="ship.weapons || ship.gold || soldTag" class="arms">
        <span v-if="soldTag" class="tag sold" :title="soldTag[1]">{{ soldTag[0] }}</span>
        <span
          v-if="ship.gold"
          class="tag"
          :class="ship.gold.status === 'done' ? 'gold' : 'goldwip'"
          :title="ship.gold.status === 'done' ? '已完成金牌标准重制' : '金牌标准重制进行中'"
        >
          {{ ship.gold.status === "done" ? "金牌标准" : "重制中" }}
        </span>
        <span v-if="armsSummaryLine">{{ armsSummaryLine }}</span>
      </p>

      <p v-if="notFlyable" class="status">
        <span class="tag concept">{{ statusChinese }}</span>
        <template v-if="ship.loaners"> · 当前借船：{{ ship.loaners }}</template>
      </p>

      <button
        type="button"
        class="link grid-toggle"
        @click="emit('openDetails', ship)"
      >
        详情
      </button>
    </div>

    <!-- 右侧采购区：WBCCU 卡片（置最左）+ 整船版本卡片（Warbond 整船 / BIS / 低强调 10 Year）+ 绿色游戏内卡片 -->
    <div class="buy">
      <!-- 独立分列的 WBCCU 卡片：置最左展示，即使无单船 Warbond 也独立呈现 -->
      <a
        v-for="up in warbondUpgradeCards"
        :key="`wb-up-${up.sku}`"
        class="ed ed-wbccu"
        :href="CCU_TOOL_URL"
        target="_blank"
        rel="noopener"
      >
        <span class="label" translate="no">
          {{ /bis/i.test(up.label) ? "BIS WBCCU" : "WBCCU" }}
        </span>
        <span class="price" translate="no">
          <s v-if="ship.ccu && up.price < ship.ccu.value">
            {{ moneyFromCents(ship.ccu.value) }}
          </s>
          {{ moneyFromCents(up.price) }}
        </span>
        <span class="ins">
          {{
            fromKey && costsMap && String(up.sku) in costsMap
              ? `补差 ${moneyFromCents(costsMap[String(up.sku)])}`
              : "CCU 升级目标价"
          }}
        </span>
        <span v-if="ship.ccu && up.price < ship.ccu.value" class="save">
          立省 {{ moneyFromCents(ship.ccu.value - up.price) }}
        </span>
      </a>

      <a
        v-for="(ed, idx) in editionCards"
        :key="`${ed.label}-${idx}`"
        :class="editionCssClass(ed)"
        :href="ed.url || ship.rsi || '#'"
        target="_blank"
        rel="noopener"
      >
        <span class="label" translate="no">{{ formatEditionLabel(ed) }}</span>
        <span class="price" translate="no">
          <s v-if="ed.list_price && ed.price < ed.list_price">
            {{ moneyFromCents(ed.list_price) }}
          </s>
          {{ moneyFromCents(ed.price) }}
        </span>
        <span v-if="ed.insurance" class="ins" :class="{ lti: ed.insurance === 'Lifetime' }">
          {{ formatInsurance(ed.insurance) }}
        </span>
        <span v-if="ed.modules.length" class="extra mods">
          + {{ ed.modules.join(" / ") }} 模块
        </span>
        <span
          v-if="ed.paints"
          class="extra"
          :title="ed.includes.join(', ')"
        >
          + {{ ed.paints }} 款涂装
        </span>
        <span v-if="editionSaving(ed)" class="save">
          立省 {{ moneyFromCents(editionSaving(ed)) }}
        </span>
        <span
          v-if="ed === standardEdition && estimateUpgradeDiff"
          class="up"
          title="价格估算（未启用或尚未完成真实路径查询）"
        >
          ≈ 升级补 +{{ moneyFromCents(estimateUpgradeDiff) }}
        </span>
        <span v-if="editionState(ed)" class="state">{{ editionState(ed) }}</span>
      </a>

      <!-- 绿色游戏内购置/租赁卡片 -->
      <div v-if="buyMin" class="ed ed-game" title="游戏内最低购买价格">
        <span class="label">游戏内购买</span>
        <span class="price" translate="no">{{ formatAuec(buyMin) }}</span>
        <span class="ins">
          {{ valuePerDollarText || `≈ ${formatHoursLabel(buyMin / rate)} 肝度` }}
        </span>
      </div>
      <div v-else-if="rentMin" class="ed ed-game" title="游戏内最低租赁价格">
        <span class="label">游戏内租赁</span>
        <span class="price" translate="no">{{ formatAuec(rentMin) }}</span>
        <span class="ins">每日</span>
      </div>
    </div>

    <!-- 模块行 -->
    <div v-if="ship.modules.length || modulePacks.length" class="ccu mods">
      <template v-if="ship.modules.length">
        <span class="k">单卖模块</span>
        <a
          v-for="mod in ship.modules"
          :key="mod.name"
          class="u"
          :href="mod.url"
          target="_blank"
          rel="noopener"
        >
          {{ mod.name }} <b>{{ moneyFromCents(mod.price) }}</b>{{ mod.available ? "" : " (已售罄)" }}
        </a>
      </template>
      <template v-if="modulePacks.length">
        <span class="k">含模块套装</span>
        <a
          v-for="(pack, idx) in modulePacks"
          :key="`pack-${idx}`"
          class="u"
          :href="pack.url"
          target="_blank"
          rel="noopener"
        >
          {{ pack.modules.length > 1 ? `全 ${pack.modules.length} 模块` : pack.modules[0] }}
          <b>{{ moneyFromCents(pack.price) }}</b>
        </a>
      </template>
    </div>

    <!-- CCU 升级价值行（仅明确 Warbond SKU 标记 WBCCU；10 Year 与普通 BIS 不计入） -->
    <div v-if="ship.upgrades.length && fromKey !== ship.key" class="ccu">
      <template v-if="fromKey && costsMap">
        <template v-if="matchingFromUpgrades.length">
          <span class="k">从 {{ fromName }} 升级实付</span>
          <span
            v-for="u in matchingFromUpgrades"
            :key="u.sku"
            class="u"
            :class="{ deal: Boolean(u.is_warbond) }"
          >
            <b>{{ moneyFromCents(costsMap[String(u.sku)]) }}</b> {{ u.label }}
          </span>
          <a class="tool" :href="CCU_TOOL_URL" target="_blank" rel="noopener">前往 RSI 机库购买 ↗</a>
          <button
            v-if="plannerEnabled && ship.ccu"
            type="button"
            class="link mine"
            @click="emit('planRoute', { fromKey, toRef: ship.ccu.ref })"
          >
            规划升级路线
          </button>
        </template>
        <template v-else>
          <span class="muted">
            当前在售升级 ({{ ship.upgrades.map((u) => u.label).join(", ") }})，但暂无从你的
            {{ fromName }} 直接生效的选项
          </span>
          <button
            v-if="plannerEnabled && ship.ccu"
            type="button"
            class="link mine"
            @click="emit('planRoute', { fromKey, toRef: ship.ccu.ref })"
          >
            规划升级路线
          </button>
        </template>
      </template>

      <template v-else>
        <span class="k">
          升级价值
          <b v-if="ship.ccu" class="ccu-val">{{ moneyFromCents(ship.ccu.value) }}</b>
          <button
            type="button"
            class="why"
            :aria-expanded="explainWhy"
            aria-label="什么是升级价值"
            @click="explainWhy = !explainWhy"
          >
            ?
          </button>
        </span>
        <button type="button" class="link mine" @click="emit('pickFromShip', ship)">
          我需要补多少？
        </button>
        <button
          v-if="plannerEnabled && ship.ccu"
          type="button"
          class="link mine"
          @click="emit('planRoute', { fromKey: '', toRef: ship.ccu.ref })"
        >
          规划升级路线
        </button>

        <Transition name="holo-expand">
          <p v-if="explainWhy && lowExampleUpgrade" class="explain">
            升级 (CCU) 用于将你机库中已有的船升级至目标舰船，实付金额约为「目标升级价值 - 原船价值」：
            <template v-if="lowExampleUpgrade.price > 10000">
              以一艘 $100 的船为例，购买 {{ lowExampleUpgrade.label }} 升级包需补
              {{ moneyFromCents(lowExampleUpgrade.price - 10000) }}。
            </template>
            升级包需在 RSI 官网「My Hangar → Ship Upgrades」中选购；部分 Warbond 优惠限定特定起跳船，
            点击「我需要补多少？」选择你的可升级船即可向 RSI 核实准确差价。
            注：RSI 升级商店（Ship Upgrades）与单船商店（Standalone Ships）相互独立，官方常在日常或活动期间单独放出 WBCCU 而不上架对应单船 Warbond 礼盒，因此可能出现下方有 WBCCU 卡片但无整船 Warbond 的情况。
            <a class="tool" :href="CCU_TOOL_URL" target="_blank" rel="noopener">前往 RSI 机库 ↗</a>
          </p>
        </Transition>
      </template>
    </div>

    <!-- 最近可升级船条（WBCCU 使用绿底色，普通 CCU 使用中性灰底色） -->
    <div v-if="nearestUpgradeRows.length && !fromKey" class="nearest-rows">
      <div
        v-for="row in nearestUpgradeRows"
        :key="row.key"
        class="nearest-row"
        :class="row.isWarbond ? 'is-wb' : 'is-std'"
      >
        <span class="nearest-title">最近可升级自 ({{ row.tagLabel }})：</span>
        <span
          v-for="cand in row.candidates"
          :key="`${row.key}-${cand.name_en}`"
          class="nearest-pill"
          :class="{ wb: row.isWarbond, always: cand.always_available }"
          :title="`${cand.name_en} · 估值 $${cand.value}${cand.always_available ? ' · 常驻无限制可升级船' : ''}`"
        >
          <span>{{ formatShipDisplay(cand.name_en, cand.name_zh, appStore.languageMode) }}</span>
          <b>+${{ cand.upgrade_cost }}</b>
          <span v-if="cand.always_available" class="nearest-tag">常驻</span>
        </span>
      </div>
    </div>

    <p v-if="!ship.in_store && !ship.upgrades.length" class="notin">
      暂未在商店上架<template v-if="lastSeenIso">，上次在售 {{ formatAgo(lastSeenIso) }}</template>
    </p>
  </li>
</template>
