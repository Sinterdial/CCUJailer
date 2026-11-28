import type { LanguageMode } from "../types/api";

export const DEFAULT_SCTOOLBOX_I18N_URL =
  "https://raw.githubusercontent.com/StarCitizenToolBox/LocalizationData/main/chinese_(simplified)/global.ini";
export const DEFAULT_SCTOOLBOX_I18N_MIRROR_URL =
  "https://git.scbox.xkeyc.cn/SCToolBox/LocalizationData/raw/branch/main/chinese_(simplified)/global.ini";

export const MANUFACTURER_ZH_MAP: Record<string, string> = {
  aegs: "圣盾动力",
  "aegis dynamics": "圣盾动力",
  anvl: "铁砧航天",
  "anvil aerospace": "铁砧航天",
  xnaa: "奥波亚",
  xian: "奥波亚",
  aopoa: "奥波亚",
  argo: "南船座宇航",
  "argo astronautics": "南船座宇航",
  banu: "巴努",
  cnou: "联合外域",
  "consolidated outland": "联合外域",
  crus: "十字军工业",
  "crusader industries": "十字军工业",
  drak: "德雷克行星际",
  "drake interplanetary": "德雷克行星际",
  espr: "埃斯佩里亚",
  esperia: "埃斯佩里亚",
  gama: "盖塔克制造",
  "gatac manufacture": "盖塔克制造",
  grey: "格雷黑市",
  "grey's market": "格雷黑市",
  grin: "灰猫工业",
  "greycat industrial": "灰猫工业",
  krig: "克鲁格星际",
  "kruger intergalactic": "克鲁格星际",
  misc: "武藏工业",
  mrai: "未来",
  mirai: "未来",
  orig: "起源跃动工厂",
  "origin jumpworks": "起源跃动工厂",
  rsi: "罗伯茨太空工业",
  "roberts space industries": "罗伯茨太空工业",
  tmbl: "盾博尔地面系统",
  tumbril: "盾博尔地面系统",
  vncl: "剜度",
  vanduul: "剜度",
};

export const SHIP_SIZES_ORDER = ["Vehicle", "Snub", "Small", "Medium", "Large", "Capital"] as const;

export const SHIP_SIZE_ZH: Record<string, string> = {
  vehicle: "地面载具",
  snub: "微型",
  small: "小型",
  medium: "中型",
  large: "大型",
  capital: "主力舰",
};

export const SHIP_ROLE_ZH: Record<string, string> = {
  "anti-air": "防空",
  "anti-vehicle": "反装甲",
  battlecruiser: "战列巡洋舰",
  boarding: "登船突击",
  bomber: "轰炸机",
  cargo: "货运",
  combat: "战斗",
  competition: "竞速",
  corvette: "护卫舰",
  destroyer: "驱逐舰",
  dropship: "空降艇",
  expedition: "远征探索",
  exploration: "探索",
  frigate: "巡防舰",
  generalist: "通用多面手",
  ground: "地面载具",
  gunship: "炮艇",
  "gun ship": "炮艇",
  "heavy bomber": "重型轰炸机",
  "heavy construction": "重型建造",
  "heavy dropship": "重型空降艇",
  "heavy fighter": "重型战斗机",
  "heavy freight": "重型货运",
  "heavy gunship": "重型炮艇",
  "heavy mining": "重型采矿",
  "heavy refueling": "重型加油",
  "heavy repair": "重型维修",
  "heavy salvage": "重型打捞",
  "heavy science": "重型科研",
  "heavy tank": "重型坦克",
  industrial: "工业",
  interceptor: "截击机",
  interdiction: "量子拦截",
  "light carrier": "轻型航母",
  "light fighter": "轻型战斗机",
  "light freight": "轻型货运",
  "light mining": "轻型采矿",
  "light refueling": "轻型加油",
  "light salvage": "轻型打捞",
  "light science": "轻型科研",
  "light tank": "轻型坦克",
  "luxury touring": "奢华巡游",
  medical: "医疗救援",
  "medium data": "中型数据",
  "medium fighter": "中型战斗机",
  "medium freight": "中型货运",
  "medium mining": "中型采矿",
  "medium refuel": "中型加油",
  "medium refueling": "中型加油",
  "medium repair": "中型维修",
  "medium salvage": "中型打捞",
  military: "军用",
  minelayer: "布雷舰",
  mining: "采矿",
  modular: "模块化",
  multi: "多用途",
  "multi-role": "多用途",
  passenger: "客运",
  pathfinder: "探路者",
  racing: "竞速",
  recovery: "打捞回收",
  refinery: "精炼",
  refining: "精炼",
  reporting: "新闻采访",
  "snub carrier": "微型舰母舰",
  "snub fighter": "微型战斗机",
  starter: "新手入门",
  "stealth bomber": "隐身轰炸机",
  "stealth fighter": "隐身战斗机",
  support: "支援保障",
  touring: "巡游",
  transport: "运输",
  transporter: "运输",
};

export function getManufacturerZh(maker: string, makerCode = ""): string {
  const byName = MANUFACTURER_ZH_MAP[maker.trim().toLowerCase()];
  if (byName) {
    return byName;
  }
  const byCode = MANUFACTURER_ZH_MAP[makerCode.trim().toLowerCase()];
  return byCode || "";
}

export function formatManufacturer(
  maker: string,
  makerCode = "",
  mode: LanguageMode = "bilingual"
): string {
  const cleanEn = maker.trim();
  if (!cleanEn) {
    return "";
  }
  const zh = getManufacturerZh(cleanEn, makerCode);
  if (!zh || mode === "en") {
    return cleanEn;
  }
  if (mode === "zh") {
    return zh;
  }
  return zh === cleanEn ? cleanEn : `${zh} / ${cleanEn}`;
}

export function formatShipSizeZh(size: string): string {
  const clean = size.trim();
  if (!clean) {
    return "";
  }
  return SHIP_SIZE_ZH[clean.toLowerCase()] || clean;
}

export function formatShipRoleZh(role: string): string {
  const clean = role.trim();
  if (!clean) {
    return "";
  }
  const direct = SHIP_ROLE_ZH[clean.toLowerCase()];
  if (direct) {
    return direct;
  }
  const parts = clean
    .split("/")
    .map((part) => {
      const token = part.trim();
      if (!token) {
        return "";
      }
      return SHIP_ROLE_ZH[token.toLowerCase()] || token;
    })
    .filter(Boolean);
  return parts.join(" / ");
}

export type StoreFilterGroup = "editions" | "sold" | "upgrades" | "game" | "ship";

export interface StoreFilterMeta {
  key: string;
  label: string;
  group: StoreFilterGroup;
}

export const STORE_FILTER_META: StoreFilterMeta[] = [
  { key: "warbond", label: "单船 Warbond", group: "editions" },
  { key: "anniversary", label: "10 Year", group: "editions" },
  { key: "lti", label: "终身保险 (LTI)", group: "editions" },
  { key: "sale", label: "单船标价折扣", group: "editions" },
  { key: "limited", label: "限量库存", group: "editions" },
  { key: "always", label: "常驻在售", group: "sold" },
  { key: "saleonly", label: "限时开售 / 限量", group: "sold" },
  { key: "deals", label: "仅 WBCCU", group: "upgrades" },
  { key: "ccu", label: "可升级 (CCU 在售)", group: "upgrades" },
  { key: "buyable", label: "游戏内可买", group: "game" },
  { key: "rentable", label: "游戏内可租", group: "game" },
  { key: "gold", label: "金牌标准", group: "ship" },
  { key: "concept", label: "画饼中 / 未可飞", group: "ship" },
];

export const STORE_SORT_OPTIONS: { key: string; label: string }[] = [
  { key: "price-desc", label: "价格：从高到低" },
  { key: "price-asc", label: "价格：从低到高" },
  { key: "released", label: "可飞版本：最新优先" },
  { key: "name", label: "舰船名称" },
  { key: "size", label: "舰船体型：从大到小" },
  { key: "game", label: "游戏内售价：从低到高" },
  { key: "game-desc", label: "游戏内售价：从高到低" },
  { key: "value", label: "性价比：每美元最高 aUEC" },
  { key: "pilot", label: "主驾火力：从高到低" },
];
