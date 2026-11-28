<script setup lang="ts">
import { computed, ref } from "vue";
import type { StoreShip } from "../types/api";

const props = defineProps<{
  ship: StoreShip;
}>();

const pickIndex = ref(0);

const gridOptions = computed(() => {
  const opts: [string, NonNullable<StoreShip["grid"]>][] = [];
  if (props.ship.grid) {
    opts.push(["标准布局", props.ship.grid]);
  }
  for (const [modName, grid] of Object.entries(props.ship.module_grids || {})) {
    opts.push([`${modName} 模块`, grid]);
  }
  return opts;
});

const currentGrid = computed(() => {
  if (gridOptions.value.length === 0) {
    return null;
  }
  const idx = Math.min(pickIndex.value, gridOptions.value.length - 1);
  return gridOptions.value[idx][1];
});

function scuOfDims(dims: string): number {
  return dims.split(" × ").reduce((acc, part) => acc * Number(part), 1);
}

const layoutMetrics = computed(() => {
  const grid = currentGrid.value;
  if (!grid || !grid.boxes.length) {
    return null;
  }
  const xs = grid.boxes.flatMap((b) => [b[0], b[0] + b[3]]);
  const zs = grid.boxes.flatMap((b) => [b[1], b[1] + b[4]]);
  const tall = Math.max(...zs) - Math.min(...zs) > (Math.max(...xs) - Math.min(...xs)) * 1.3;
  const boxes = tall
    ? grid.boxes.map(([x, z, y, bw, bl, bh]) => ({
        x: z,
        z: x,
        y,
        bw: bl,
        bl: bw,
        bh,
        label: `${bw}×${bl}×${bh}`,
      }))
    : grid.boxes.map(([x, z, y, bw, bl, bh]) => ({
        x,
        z,
        y,
        bw,
        bl,
        bh,
        label: `${bw}×${bl}×${bh}`,
      }));

  const minX = Math.min(...boxes.map((b) => b.x));
  const minZ = Math.min(...boxes.map((b) => b.z));
  const maxX = Math.max(...boxes.map((b) => b.x + b.bw));
  const maxZ = Math.max(...boxes.map((b) => b.z + b.bl));
  const maxH = Math.max(...boxes.map((b) => b.y + b.bh), 1);
  const w = maxX - minX;
  const h = maxZ - minZ;
  const pad = Math.max(w, h) * 0.02;
  const span = Math.max(w, h);

  const labelled = new Set<number>();
  const taken: [number, number, number, number][] = [];
  const order = [...boxes.keys()].sort(
    (i, j) => boxes[j].bw * boxes[j].bl - boxes[i].bw * boxes[i].bl
  );
  for (const idx of order) {
    const b = boxes[idx];
    const cx = b.x + b.bw / 2;
    const cz = b.z + b.bl / 2;
    if (taken.some(([tx, tz, tw, tl]) => cx > tx && cx < tx + tw && cz > tz && cz < tz + tl)) {
      continue;
    }
    if (
      taken.some(
        ([tx, tz, tw, tl]) =>
          tx + tw / 2 > b.x &&
          tx + tw / 2 < b.x + b.bw &&
          tz + tl / 2 > b.z &&
          tz + tl / 2 < b.z + b.bl
      )
    ) {
      continue;
    }
    labelled.add(idx);
    taken.push([b.x, b.z, b.bw, b.bl]);
  }

  const groups: Record<string, number> = {};
  for (const [, , , bw, bl, bh] of grid.boxes) {
    const k = `${bw} × ${bl} × ${bh}`;
    groups[k] = (groups[k] || 0) + 1;
  }
  const rows = Object.entries(groups).sort((a, z) => scuOfDims(z[0]) - scuOfDims(a[0]));

  return {
    grid,
    boxes,
    viewBox: `${minX - pad} ${minZ - pad} ${w + pad * 2} ${h + pad * 2}`,
    maxH,
    span,
    labelled,
    rows,
  };
});
</script>

<template>
  <div v-if="gridOptions.length" class="cargo">
    <div v-if="gridOptions.length > 1" class="chips" role="group" aria-label="货舱模块布局">
      <button
        v-for="([label, g], idx) in gridOptions"
        :key="label"
        type="button"
        class="chip"
        :aria-pressed="idx === pickIndex"
        @click="pickIndex = idx"
      >
        {{ label }}
        <span class="n">{{ g.scu.toLocaleString("en-US") }} SCU</span>
      </button>
    </div>

    <figure v-if="layoutMetrics" class="grid">
      <svg
        :viewBox="layoutMetrics.viewBox"
        role="img"
        :aria-label="`俯视货舱网格 ${layoutMetrics.grid.scu} SCU`"
        preserveAspectRatio="xMidYMid meet"
      >
        <rect
          v-for="(box, i) in layoutMetrics.boxes"
          :key="`r-${i}`"
          :x="box.x"
          :y="box.z"
          :width="box.bw"
          :height="box.bl"
          :style="{ fillOpacity: 0.35 + 0.5 * ((box.y + box.bh) / layoutMetrics.maxH) }"
        />
        <template v-for="(box, i) in layoutMetrics.boxes" :key="`t-${i}`">
          <text
            v-if="
              layoutMetrics.labelled.has(i) &&
              Math.min(box.bw, box.bl) * 0.3 >= layoutMetrics.span * 0.022
            "
            :x="box.x + box.bw / 2"
            :y="box.z + box.bl / 2"
            :font-size="Math.min(Math.min(box.bw, box.bl) * 0.3, layoutMetrics.span * 0.05)"
            text-anchor="middle"
            dominant-baseline="central"
          >
            {{ box.label }}
          </text>
        </template>
      </svg>

      <figcaption>
        <ul class="boxes">
          <li v-for="[dims, cnt] in layoutMetrics.rows" :key="dims">
            <b>{{ cnt }} ×</b> {{ dims }}
            <span>= {{ (cnt * scuOfDims(dims)).toLocaleString("en-US") }} SCU</span>
          </li>
        </ul>
        <p>
          共 {{ layoutMetrics.grid.scu.toLocaleString("en-US") }} SCU{{ layoutMetrics.grid.concept ? "（画饼阶段布局）" : "" }}。箱格尺寸单位为宽 × 长 × 高（SCU），颜色越深代表可堆叠层数越高。
          <a :href="layoutMetrics.grid.viewer" target="_blank" rel="noopener">前往 sc-cargo.space 查看 3D 视图 ↗</a>
        </p>
      </figcaption>
    </figure>
  </div>
</template>
