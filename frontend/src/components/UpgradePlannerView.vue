<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { apiClient } from "../api/client";
import {
  appStore,
  formatAgo,
  formatShipDisplay,
  moneyFromCents,
  notify,
  updateUserPreferences,
} from "../composables/useAppStore";
import type { UpgradePlanResponse } from "../types/api";

const props = defineProps<{
  initialFromKey?: string;
  initialToRef?: number | null;
}>();

interface PlanShip {
  ref: number;
  name_en: string;
  name_zh: string;
  value: number;
  in_store: boolean;
  always: boolean;
}

interface PlanEdge {
  from: number;
  to: number;
  cost: number;
  label: string;
  owned?: boolean;
}

interface PlanRoute {
  hops: PlanEdge[];
  cost: number;
}

const OWNED_STORAGE_KEY = "ccujailer_owned_ccus";
const CCU_TOOL_URL = "https://robertsspaceindustries.com/en/account/pledges";
const ALTERNATIVES_COUNT = 3;

const loading = ref(false);
const planMap = ref<UpgradePlanResponse | null>(null);
const fromRef = ref<number | "">("");
const toRef = ref<number | "">("");
const showOwned = ref(false);
const ownedAddFrom = ref<number | "">("");
const ownedAddTo = ref<number | "">("");
const ownedPairs = ref<[number, number][]>(loadOwned());

function loadOwned(): [number, number][] {
  if (appStore.authStatus.authenticated && Array.isArray(appStore.userPreferences.owned_ccus)) {
    return appStore.userPreferences.owned_ccus.filter(
      (p): p is [number, number] => Array.isArray(p) && p.length === 2
    );
  }
  try {
    const raw = JSON.parse(localStorage.getItem(OWNED_STORAGE_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((p) => Array.isArray(p) && p.length === 2) : [];
  } catch {
    return [];
  }
}

function saveOwned(next: [number, number][]): void {
  ownedPairs.value = next;
  try {
    localStorage.setItem(OWNED_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 忽略本地存储写入异常
  }
  void updateUserPreferences({ owned_ccus: next }, { immediate: true });
}

watch(
  () => appStore.preferencesSyncVersion,
  () => {
    if (appStore.authStatus.authenticated && Array.isArray(appStore.userPreferences.owned_ccus)) {
      ownedPairs.value = appStore.userPreferences.owned_ccus.filter(
        (p): p is [number, number] => Array.isArray(p) && p.length === 2
      );
    }
  }
);

const ships = computed<PlanShip[]>(() => {
  if (!planMap.value) {
    return [];
  }
  return planMap.value.ships
    .map(([ref, name_en, name_zh, value, in_store, always]) => ({
      ref,
      name_en,
      name_zh,
      value,
      in_store,
      always,
    }))
    .sort((a, b) =>
      a.name_en.localeCompare(b.name_en, "en", { numeric: true, sensitivity: "base" })
    );
});

const byRef = computed<Record<number, PlanShip>>(() => {
  const m: Record<number, PlanShip> = {};
  for (const s of ships.value) {
    m[s.ref] = s;
  }
  return m;
});

function shipTitle(ship: PlanShip): string {
  return formatShipDisplay(ship.name_en, ship.name_zh, appStore.languageMode);
}

const edges = computed<Record<number, PlanEdge[]>>(() => {
  const mapData = planMap.value;
  if (!mapData) {
    return {};
  }
  const best: Record<string, PlanEdge> = {};
  const addEdge = (from: number, edge: PlanEdge) => {
    const k = `${from}>${edge.to}`;
    if (!best[k] || edge.cost < best[k].cost) {
      best[k] = edge;
    }
  };
  for (const [fromStr, skuList] of Object.entries(mapData.paths)) {
    const fromId = Number(fromStr);
    if (!byRef.value[fromId]) {
      continue;
    }
    for (const [skuId, costCents] of skuList) {
      const skuMeta = mapData.skus[String(skuId)];
      if (!skuMeta) {
        continue;
      }
      const targetRef = Number(skuMeta[0]);
      if (byRef.value[targetRef]) {
        addEdge(fromId, {
          from: fromId,
          to: targetRef,
          cost: costCents,
          label: String(skuMeta[1]),
        });
      }
    }
  }
  for (const [f, t] of ownedPairs.value) {
    if (byRef.value[f] && byRef.value[t]) {
      addEdge(f, { from: f, to: t, cost: 0, label: "", owned: true });
    }
  }
  const out: Record<number, PlanEdge[]> = {};
  for (const edge of Object.values(best)) {
    (out[edge.from] ||= []).push(edge);
  }
  return out;
});

const startShip = computed(() => (typeof fromRef.value === "number" ? byRef.value[fromRef.value] : undefined));
const goalShip = computed(() => (typeof toRef.value === "number" ? byRef.value[toRef.value] : undefined));

const reachableRefs = computed<Set<number>>(() => {
  if (!startShip.value) {
    return new Set();
  }
  const seen = new Set<number>();
  const queue = [startShip.value.ref];
  while (queue.length) {
    const cur = queue.shift()!;
    for (const edge of edges.value[cur] || []) {
      if (!seen.has(edge.to)) {
        seen.add(edge.to);
        queue.push(edge.to);
      }
    }
  }
  return seen;
});

const targetCandidates = computed(() =>
  startShip.value ? ships.value.filter((s) => s.value > startShip.value!.value) : []
);

const ownedTargetCandidates = computed(() => {
  if (typeof ownedAddFrom.value !== "number") {
    return [];
  }
  const base = byRef.value[ownedAddFrom.value];
  return base ? ships.value.filter((s) => s.value > base.value) : [];
});

const foundRoutes = computed<PlanRoute[]>(() => {
  if (!startShip.value || !goalShip.value || goalShip.value.value <= startShip.value.value) {
    return [];
  }
  const startId = startShip.value.ref;
  const goalId = goalShip.value.ref;

  const incoming: Record<number, PlanEdge[]> = {};
  for (const list of Object.values(edges.value)) {
    for (const edge of list) {
      (incoming[edge.to] ||= []).push(edge);
    }
  }
  interface State {
    cost: number;
    steps: number;
    next: PlanEdge | null;
  }
  const best: Record<number, State> = { [goalId]: { cost: 0, steps: 0, next: null } };
  const done = new Set<number>();
  const better = (a: State, b: State | undefined) =>
    !b || a.cost < b.cost || (a.cost === b.cost && a.steps < b.steps);

  for (;;) {
    let cur: number | null = null;
    for (const kStr of Object.keys(best)) {
      const k = Number(kStr);
      if (!done.has(k) && (cur === null || better(best[k], best[cur]))) {
        cur = k;
      }
    }
    if (cur === null) {
      break;
    }
    done.add(cur);
    for (const edge of incoming[cur] || []) {
      if (done.has(edge.from)) {
        continue;
      }
      const candidate: State = {
        cost: best[cur].cost + edge.cost,
        steps: best[cur].steps + 1,
        next: edge,
      };
      if (better(candidate, best[edge.from])) {
        best[edge.from] = candidate;
      }
    }
  }

  return (edges.value[startId] || [])
    .filter((firstHop) => Boolean(best[firstHop.to]))
    .map((firstHop) => {
      const hops: PlanEdge[] = [firstHop];
      while (hops[hops.length - 1].to !== goalId) {
        const nxt = best[hops[hops.length - 1].to]?.next;
        if (!nxt) {
          break;
        }
        hops.push(nxt);
      }
      return {
        hops,
        cost: hops.reduce((sum, h) => sum + h.cost, 0),
      };
    })
    .filter((route) => route.hops[route.hops.length - 1].to === goalId)
    .sort((a, b) => a.cost - b.cost || a.hops.length - b.hops.length);
});

const bestRoute = computed(() => foundRoutes.value[0] || null);
const altRoutes = computed(() => foundRoutes.value.slice(1, 1 + ALTERNATIVES_COUNT));
const directRoute = computed(() =>
  foundRoutes.value.find((r) => r.hops.length === 1 && !r.hops[0].owned) || null
);

function formatStepLabel(label: string): string {
  return /upgrade$/i.test(label) ? label : `${label} 升级`;
}

function addOwnedCcu(): void {
  if (typeof ownedAddFrom.value !== "number" || typeof ownedAddTo.value !== "number") {
    return;
  }
  if (ownedPairs.value.some(([f, t]) => f === ownedAddFrom.value && t === ownedAddTo.value)) {
    return;
  }
  saveOwned([...ownedPairs.value, [ownedAddFrom.value, ownedAddTo.value]]);
  ownedAddTo.value = "";
}

function removeOwnedAt(index: number): void {
  saveOwned(ownedPairs.value.filter((_, idx) => idx !== index));
}

function applyInitialSelection(): void {
  if (!ships.value.length) {
    return;
  }
  if (props.initialFromKey) {
    const matched = ships.value.find(
      (s) => s.name_en.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim() === props.initialFromKey
    );
    if (matched) {
      fromRef.value = matched.ref;
    }
  }
  if (!fromRef.value) {
    const alwaysLowest = ships.value
      .filter((s) => s.always && s.value > 0)
      .sort((a, b) => a.value - b.value)[0];
    if (alwaysLowest) {
      fromRef.value = alwaysLowest.ref;
    }
  }
  if (props.initialToRef && byRef.value[props.initialToRef]) {
    toRef.value = props.initialToRef;
  }
}

async function loadPlan(): Promise<void> {
  loading.value = true;
  try {
    planMap.value = await apiClient.getUpgradePlanMap();
    applyInitialSelection();
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载升级路线图失败", "error");
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.initialFromKey, props.initialToRef],
  () => {
    applyInitialSelection();
  }
);

onMounted(() => {
  void loadPlan();
});
</script>

<template>
  <div class="planner-wrap">
    <div v-if="loading && !planMap" class="empty">正在构建升级路线可达图…</div>

    <template v-else-if="planMap">
      <div class="plan-pick">
        <label class="plan-field">
          <span>我拥有的可升级船</span>
          <select v-model="fromRef">
            <option value="">请选择可升级船</option>
            <option v-for="s in ships" :key="s.ref" :value="s.ref">
              {{ shipTitle(s) }} ({{ moneyFromCents(s.value) }})
            </option>
          </select>
        </label>

        <label class="plan-field">
          <span>我希望升级至的目标船</span>
          <select v-model="toRef" :disabled="!startShip">
            <option value="">{{ startShip ? "请选择目标舰船" : "请先选择可升级船" }}</option>
            <template v-if="startShip">
              <optgroup label="当前商店可通达目标">
                <option
                  v-for="s in targetCandidates.filter((x) => reachableRefs.has(x.ref))"
                  :key="s.ref"
                  :value="s.ref"
                >
                  {{ shipTitle(s) }} ({{ moneyFromCents(s.value) }})
                </option>
              </optgroup>
              <optgroup label="当前暂无直接上架链接">
                <option
                  v-for="s in targetCandidates.filter((x) => !reachableRefs.has(x.ref))"
                  :key="s.ref"
                  :value="s.ref"
                >
                  {{ shipTitle(s) }} ({{ moneyFromCents(s.value) }})
                </option>
              </optgroup>
            </template>
          </select>
        </label>

        <button
          type="button"
          class="link owned-toggle"
          :aria-expanded="showOwned"
          @click="showOwned = !showOwned"
        >
          {{ showOwned ? "收起" : "添加" }}我机库中已提前囤好的 CCU{{
            ownedPairs.length ? ` (${ownedPairs.length})` : ""
          }}
        </button>

        <div v-if="showOwned" class="owned">
          <ul v-if="ownedPairs.length">
            <li v-for="([fId, tId], idx) in ownedPairs" :key="`${fId}-${tId}`">
              <span>
                {{ byRef[fId] ? shipTitle(byRef[fId]) : `#${fId}` }} →
                {{ byRef[tId] ? shipTitle(byRef[tId]) : `#${tId}` }}
              </span>
              <button type="button" class="link" @click="removeOwnedAt(idx)">移除</button>
            </li>
          </ul>
          <p v-else class="muted">
            如果你在之前限时活动期间已经购入过部分 CCU，可以添加到这里作为路线规划中的 $0 已知跳板（已登录账号自动同步至云端与多设备）。
          </p>

          <div class="owned-add">
            <select
              v-model="ownedAddFrom"
              aria-label="已有升级可升级船"
              @change="ownedAddTo = ''"
            >
              <option value="">可升级船…</option>
              <option v-for="s in ships" :key="s.ref" :value="s.ref">{{ shipTitle(s) }}</option>
            </select>
            <select v-model="ownedAddTo" aria-label="已有升级目标船" :disabled="!ownedAddFrom">
              <option value="">目标船…</option>
              <option v-for="s in ownedTargetCandidates" :key="s.ref" :value="s.ref">
                {{ shipTitle(s) }}
              </option>
            </select>
            <button
              type="button"
              class="add"
              :disabled="!ownedAddFrom || !ownedAddTo"
              @click="addOwnedCcu"
            >
              添加
            </button>
          </div>
        </div>
      </div>

      <p v-if="startShip && goalShip && goalShip.value <= startShip.value" class="empty">
        RSI 升级规则只能向上升级：请选择估值高于 {{ shipTitle(startShip) }} 的目标舰船。
      </p>

      <section v-else-if="startShip && goalShip && bestRoute" class="route" aria-label="推荐省钱路线">
        <div class="route-head">
          <span class="k">推荐最优链条</span>
          <b class="total">{{ moneyFromCents(bestRoute.cost) }}</b>
          <span class="vs">
            <template v-if="goalShip.value - startShip.value - bestRoute.cost > 0">
              <b class="saves">
                立省 {{ moneyFromCents(goalShip.value - startShip.value - bestRoute.cost) }}
              </b>
              （原船价差 {{ moneyFromCents(goalShip.value - startShip.value) }}）
            </template>
            <template v-else>
              原船价差 {{ moneyFromCents(goalShip.value - startShip.value) }}
            </template>
            <template v-if="directRoute && directRoute !== bestRoute">
              · 直升价格：{{ moneyFromCents(directRoute.cost) }}
            </template>
          </span>
        </div>

        <ol class="steps">
          <li class="stop">
            <span class="nm">{{ shipTitle(startShip) }}</span>
            <span class="v">{{ moneyFromCents(startShip.value) }}</span>
          </li>
          <template v-for="(hop, idx) in bestRoute.hops" :key="idx">
            <li class="hop">
              <template v-if="hop.owned">
                使用你机库已购 CCU <b>$0</b>
              </template>
              <template v-else>
                {{ formatStepLabel(hop.label) }} <b>{{ moneyFromCents(hop.cost) }}</b>
                <span v-if="/warbond/i.test(hop.label)" class="tag wb">仅限新钱 (Warbond)</span>
              </template>
            </li>
            <li v-if="byRef[hop.to]" class="stop">
              <span class="nm">{{ shipTitle(byRef[hop.to]) }}</span>
              <span class="v">{{ moneyFromCents(byRef[hop.to].value) }}</span>
            </li>
          </template>
        </ol>

        <a class="tool" :href="CCU_TOOL_URL" target="_blank" rel="noopener">
          前往 RSI 机库升级工具购买 ↗
        </a>

        <details v-if="altRoutes.length" class="others">
          <summary>查看其他备选路线 ({{ altRoutes.length }})</summary>
          <div v-for="(route, rIdx) in altRoutes" :key="rIdx" class="other">
            <p>
              <b>{{ moneyFromCents(route.cost) }}</b> ·
              {{
                route.hops.length === 1
                  ? "一步直达"
                  : `经由 ${route.hops
                      .slice(0, -1)
                      .map((h) => (byRef[h.to] ? shipTitle(byRef[h.to]) : `#${h.to}`))
                      .join(" → ")}`
              }}
            </p>
            <ol class="steps">
              <li class="stop">
                <span class="nm">{{ shipTitle(startShip) }}</span>
                <span class="v">{{ moneyFromCents(startShip.value) }}</span>
              </li>
              <template v-for="(hop, hIdx) in route.hops" :key="hIdx">
                <li class="hop">
                  <template v-if="hop.owned">使用自备 CCU <b>$0</b></template>
                  <template v-else>
                    {{ formatStepLabel(hop.label) }} <b>{{ moneyFromCents(hop.cost) }}</b>
                  </template>
                </li>
                <li v-if="byRef[hop.to]" class="stop">
                  <span class="nm">{{ shipTitle(byRef[hop.to]) }}</span>
                  <span class="v">{{ moneyFromCents(byRef[hop.to].value) }}</span>
                </li>
              </template>
            </ol>
          </div>
        </details>
      </section>

      <p v-else-if="startShip && goalShip" class="empty">
        当前商店在售 CCU 暂时无法拼出从 {{ shipTitle(startShip) }} 到 {{ shipTitle(goalShip) }} 的链路。
      </p>

      <p v-else-if="startShip" class="empty">
        当前商店与你自备 CCU 可直达 {{ reachableRefs.size }} 艘舰船，请在上方选择目标舰船。
      </p>

      <p class="plan-note">
        <template v-if="planMap.estimated">
          注：后台首次全量路径探测尚在进行，当前使用官方标价差估算链条；完整探测完成后会自动精算限制性跳转。
        </template>
        <template v-else-if="planMap.at">
          升级可达图实测自 RSI 升级商店，最近更新于 {{ formatAgo(planMap.at) }}。
        </template>
        WBCCU 仅限使用新资金购买，且促销 SKU 可能随时下架，下单前请以 RSI 官网结账金额为准。
      </p>
    </template>
  </div>
</template>
