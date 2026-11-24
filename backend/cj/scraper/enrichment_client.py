"""
第三方数据源异步采集与舰船元数据富化客户端模块。

集成上游 pledgewatch 的四大补充数据源，统一经由 cj.core.net.NetworkManager 异步拉取：
1. RSI 官方 Support 借用船列表（Loaner Ship Matrix）
2. Star Citizen Wiki MediaWiki API（实装版本、售卖规律与 Gold Standard 状态）
3. Star Citizen Wiki API + UEX API（游戏内 aUEC 买/租价格、保险理赔耗时、武器挂载与价格历史）
4. sc-cargo.space GitHub 仓库（SVG 俯视货舱网格模型）
"""

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from cj.core.logging import get_logger
from cj.core.net import NetworkManager
from cj.scraper.rsi_client import normalize_ship_key

logger = get_logger(__name__)

LOANERS_URL = (
    "https://support.robertsspaceindustries.com/api/v2/help_center/en-us/articles/360003093114.json"
)
WIKI_MEDIAWIKI_API = (
    "https://starcitizen.tools/api.php?action=query&prop=revisions"
    "&rvprop=content&rvslots=main&format=json&redirects=1&titles="
)
WIKI_PIPELINE_PAGE = "Vehicle_Pipeline"
WIKI_VEHICLES_URL = "https://api.star-citizen.wiki/api/v2/vehicles?page%5Bsize%5D=50&page%5Bnumber%5D={}"
WIKI_ITEMS_URL = (
    "https://api.star-citizen.wiki/api/v2/items?filter%5Btype%5D={}&page%5Bsize%5D=200&page%5Bnumber%5D={}"
)
UEX_BASE_URL = "https://api.uexcorp.space/2.0/"
CARGO_REPO = "sccargospace/sc-cargo.space"
CARGO_TREE_URL = f"https://api.github.com/repos/{CARGO_REPO}/git/trees/main?recursive=1"
CARGO_RAW_BASE = f"https://raw.githubusercontent.com/{CARGO_REPO}/main/"
CARGO_VIEWER_BASE = "https://sc-cargo.space/#/viewer/"

_ADDED_VERSION_RE = re.compile(r"^\|\s*addedinversion\s*=\s*(.*?)\s*$", re.MULTILINE)
_PLEDGE_AVAIL_RE = re.compile(r"^\|\s*pledgeavailability\s*=\s*(.*?)\s*$", re.MULTILINE)
_VERSION_RE = re.compile(r"^(?:Alpha|Patch)?\s*V?(\d+(?:\.\d+){1,3})[a-z]?$", re.IGNORECASE)
_WIKI_LINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]")
_PACK_RE = re.compile(r"\b(pack|package|collection|bundle)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LoanerRule:
    """
    单条官方借用船规则条目。

    @ivar raw_ship: 官方文档中记载的原始船名文本
    @ivar cover_keys: 解析出的归一化适用舰船键列表
    @ivar loaners: 发放的借用船名称文本
    """

    raw_ship: str
    cover_keys: list[str]
    loaners: str


@dataclass(frozen=True, slots=True)
class WikiReleaseBundle:
    """
    来自 starcitizen.tools Wiki 的实装补丁、售卖规律与金牌标准集合。

    @ivar released: {原船名: 实装版本号}
    @ivar availability: {原船名: always/time/quantity/retiring}
    @ivar gold_rows: [(系列或船名, done/in progress, patch)]
    """

    released: dict[str, str] = field(default_factory=dict)
    availability: dict[str, str] = field(default_factory=dict)
    gold_rows: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IngameShipData:
    """
    单艘舰船聚合后的游戏内与历史价格富化结果。

    @ivar matrix_id: Ship Matrix ID 字符串
    @ivar game_version: 数据采集时对应的游戏版本号
    @ivar class_name: 游戏客户端类名（用于拼装 Erkul 配装器外链）
    @ivar claim_time_min: 标准理赔时间（分钟）
    @ivar expedite_time_min: 加急后理赔时间（分钟）
    @ivar expedite_cost_auec: 加急费用（aUEC）
    @ivar buy_locations: 游戏内购买价格与地点列表 [[price, terminal, ts], ...]
    @ivar rent_locations: 游戏内租赁价格与地点列表 [[price, terminal, ts], ...]
    @ivar weapons_summary: 武器配置与火力汇总字典
    @ivar price_history: 历史上架价格轨迹列表 [[date, usd, warbond, source, title], ...]
    """

    matrix_id: str
    game_version: str
    class_name: str
    claim_time_min: float | None
    expedite_time_min: float | None
    expedite_cost_auec: int | None
    buy_locations: list[Any]
    rent_locations: list[Any]
    weapons_summary: dict[str, Any] | None
    price_history: list[Any]


@dataclass(frozen=True, slots=True)
class CargoGridRecord:
    """
    单份标准货舱网格布局记录。

    @ivar key: 归一化布局主名称
    @ivar name: 原始名称
    @ivar alt_keys: 归一化别名集合
    @ivar scu: 官方货舱 SCU 总量
    @ivar boxes: 货舱箱体坐标列表 [[x, z, y, w, l, h], ...]
    @ivar viewer: sc-cargo.space 3D 查看器链接
    @ivar concept: 是否属于概念未完成布局
    """

    key: str
    name: str
    alt_keys: list[str]
    scu: float
    boxes: list[list[Any]]
    viewer: str
    concept: bool


def parse_loaners_html(body: str) -> list[LoanerRule]:
    """
    解析 RSI Support 借用船说明页 HTML 表格，生成结构化借用规则列表。

    @param body: Support 文章正文 HTML 字符串
    @return: LoanerRule 列表
    """
    rules: list[LoanerRule] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.DOTALL):
        cells = [
            " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cell)).split())
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
        ]
        if len(cells) < 2 or not cells[0] or cells[0].upper() == "YOUR SHIP":
            continue
        raw_name = cells[0]
        parts = [p for p in re.split(r"\s*(?:,|/|&|\band\b)\s*", raw_name) if p.strip()]
        first = normalize_ship_key(parts[0]).split()[0] if parts else ""
        cover_keys: list[str] = []
        for part in parts:
            key = normalize_ship_key(re.sub(r"\bvariants?\b", "", part, flags=re.IGNORECASE))
            if cover_keys and first and key and not key.startswith(first):
                key = f"{first} {key}"
            if key:
                cover_keys.append(key)
        rules.append(LoanerRule(raw_ship=raw_name, cover_keys=cover_keys, loaners=cells[1]))
    return rules


def _loaner_matches_key(loaner_key: str, ship_key: str) -> bool:
    """
    判断借用表键名是否覆盖指定 Ship Matrix 舰船键名。

    @param loaner_key: 借用规则中的归一化船名键
    @param ship_key: 目标舰船归一化键
    @return: 若覆盖返回 True
    """
    return (
        loaner_key == ship_key
        or ship_key.startswith(f"{loaner_key} ")
        or loaner_key.startswith(f"{ship_key} ")
        or (
            len(loaner_key) >= 3
            and ship_key.startswith(loaner_key)
            and ship_key[len(loaner_key) :].isalpha()
            and len(ship_key) - len(loaner_key) <= 2
        )
    )


def match_loaner_for_key(ship_key: str, rules: list[LoanerRule]) -> str:
    """
    为指定舰船匹配公式借用船说明文案。

    @param ship_key: 目标舰船归一化键
    @param rules: 已解析的借用船规则列表
    @return: 借用船文本；未匹配返回空字符串
    """
    for rule in rules:
        if any(_loaner_matches_key(ck, ship_key) for ck in rule.cover_keys):
            return rule.loaners
    return ""


def parse_wiki_patch(wikitext: str) -> str:
    """
    从 Wiki Infobox Wikitext 中提取实装版本号。

    @param wikitext: Wiki 页面原文
    @return: 版本号字符串（如 4.4.0，机库模块记为 0，概念船占位返回空串）
    """
    matched = _ADDED_VERSION_RE.search(wikitext or "")
    value = re.sub(r"<[^>]+>.*$|\{\{.*$", "", matched.group(1)).strip() if matched else ""
    if value.lower() == "hangar module":
        return "0"
    ver = _VERSION_RE.match(value)
    return ver.group(1) if ver else ""


def parse_wiki_availability(wikitext: str) -> str:
    """
    从 Wiki Infobox 中提取舰船商店售卖规律（always/time/quantity/retiring）。

    @param wikitext: Wiki 页面原文
    @return: 售卖规律标识或空字符串
    """
    matched = _PLEDGE_AVAIL_RE.search(wikitext or "")
    value = matched.group(1).lower() if matched else ""
    for keyword, label in (
        ("always", "always"),
        ("time", "time"),
        ("quantity", "quantity"),
        ("retiring", "retiring"),
    ):
        if keyword in value:
            return label
    return ""


def parse_gold_standard_rows(wikitext: str) -> list[tuple[str, str, str]]:
    """
    从 Vehicle_Pipeline 页面中解析 Gold Standard 舰船与进度表格。

    @param wikitext: Vehicle_Pipeline 页面原文
    @return: [(船名/系列名, done|in progress, patch)] 列表
    """
    start = (wikitext or "").find("List of gold standard vehicles")
    if start < 0:
        return []
    end = wikitext.find("|}", start)
    table = wikitext[start : end if end > start else len(wikitext)]
    rows: list[tuple[str, str, str]] = []
    for raw_row in re.split(r"\n\s*\|-", table)[1:]:
        cells: list[str] = []
        for line in raw_row.splitlines():
            stripped = line.strip()
            if stripped.startswith("|"):
                cells.extend(part.strip() for part in stripped.lstrip("|").split("||"))
        if len(cells) < 4:
            continue
        link_match = _WIKI_LINK_RE.search(cells[0])
        patch_match = _VERSION_RE.match(
            re.sub(r"\{\{Alpha\|([^}]*)\}\}", r"\1", cells[3]).strip()
        )
        if link_match:
            done = cells[2].lower() == "implemented"
            status_str = "done" if done else "in progress"
            patch_str = patch_match.group(1) if patch_match and done else ""
            rows.append((link_match.group(1).strip(), status_str, patch_str))
    return rows


def fill_variant_values(
    base_map: dict[str, str],
    all_names: list[str],
) -> dict[str, str]:
    """
    按词集合包含关系将母船的实装补丁或售卖规律遗传给衍生涂装/纪念版变体。

    @param base_map: {原船名: 字段值}
    @param all_names: 需要补全的全量船名列表
    @return: 补全后的 {船名: 字段值}
    """
    words_by_name = {name: set(normalize_ship_key(name).split()) for name in base_map}
    output = dict(base_map)
    for name in all_names:
        if name in output:
            continue
        mine = set(normalize_ship_key(name).split())
        candidates = [cand for cand, words in words_by_name.items() if words and words <= mine]
        if candidates:
            best = max(candidates, key=lambda c: len(words_by_name[c]))
            output[name] = base_map[best]
    return output


def resolve_gold_for_ship(
    ship_name: str,
    gold_rows: list[tuple[str, str, str]],
) -> tuple[str, str, str]:
    """
    匹配单艘舰船的 Gold Standard 状态、完成版本与所属系列名称。

    @param ship_name: 舰船英文原名
    @param gold_rows: Gold Standard 表格列表
    @return: (gold_status, gold_patch, gold_family)
    """
    if not gold_rows:
        return "", "", ""
    words_map = {
        name: set(normalize_ship_key(name).split()) - {"starlifter"}
        for name, _, _ in gold_rows
    }
    row_map = {name: (st, patch) for name, st, patch in gold_rows}
    mine = set(normalize_ship_key(ship_name).split())
    hits = [name for name, w in words_map.items() if w and w <= mine]
    if not hits:
        return "", "", ""
    best = max(hits, key=lambda n: len(words_map[n]))
    status_str, patch_str = row_map[best]
    family = "" if words_map[best] == mine else best
    return status_str, patch_str, family


def _extract_rack_spec(rack_item: dict[str, Any]) -> tuple[int, int | None, str]:
    """
    解析 Wiki 导弹/炸弹挂架规格（容量、尺寸与弹药分类）。

    @param rack_item: MissileLauncher 装备字典
    @return: (载弹量, 导弹尺寸, missile|torpedo|bomb)
    """
    kinds: set[str] = set()
    for port in rack_item.get("ports") or []:
        for compat in port.get("compatible_types") or []:
            kinds.update(compat.get("sub_types") or [])
    kind = "torpedo" if "Torpedo" in kinds else "bomb" if "Bomb" in kinds else "missile"
    mr = rack_item.get("missile_rack") or {}
    return int(mr.get("missile_count") or 0), mr.get("missile_size"), kind


def summarize_wiki_weapons(
    vehicle: dict[str, Any],
    sizes: dict[str, int | None],
    classes: dict[str, tuple[str, int | None]],
    racks: dict[str, tuple[int, int | None, str]],
) -> dict[str, Any] | None:
    """
    汇总单艘舰船原厂火炮、炮塔控制权与导弹挂载数据，供前端卡片与武器表展示。

    @param vehicle: Star Citizen Wiki API 单个载具记录
    @param sizes: 武器名称到尺寸映射
    @param classes: 武器 class_name 到 (名称, 尺寸) 映射
    @param racks: 导弹架 UUID / class_name 到规格映射
    @return: 武器汇总字典或 None
    """
    weaponry = vehicle.get("weaponry")
    turrets = vehicle.get("turrets") or {}
    if not weaponry:
        return None
    fixed = (weaponry.get("fixed_weapons") or {}).get("weapons") or []

    def _kit(guns: list[dict[str, Any]], fallback: list[Any] = ()) -> list[list[Any]]:  # type: ignore[assignment]
        counts: dict[tuple[Any, str], int] = {}
        for idx, gun in enumerate(guns):
            g_name = str(gun.get("name") or "?")
            g_size = gun.get("size") or sizes.get(g_name) or (
                fallback[idx] if idx < len(fallback) else None
            )
            counts[(g_size, g_name)] = counts.get((g_size, g_name), 0) + 1
        return [
            [cnt, sz, nm]
            for (sz, nm), cnt in sorted(counts.items(), key=lambda x: (-(x[0][0] or 0), x[0][1]))
        ]

    def _damage(guns: list[dict[str, Any]]) -> list[float]:
        return [
            float(sum((gun.get(field_name) or 0) for gun in guns))
            for field_name in ("dps", "sustained_dps", "alpha")
        ]

    rows: dict[tuple[str, str, str], list[Any]] = {}
    missiles: dict[tuple[Any, str], int] = {}

    if fixed:
        rows[("pilot", "hardpoints", "")] = [1, _kit(fixed), _damage(fixed)]

    for mount in ("manned", "remote", "pdc"):
        for item in turrets.get(mount) or []:
            guns = item.get("weapons") or []
            who = (
                "auto"
                if mount == "pdc"
                else "both"
                if item.get("is_pilot_slaveable")
                else "gunner"
            )
            each = _kit(guns, item.get("weapon_sizes") or [])
            kind = mount
            if not guns:
                fitted = item.get("payload_class_names") or []
                held = [racks[c] for c in fitted if c in racks]
                named = [classes[c] for c in fitted if c in classes]
                if held:
                    each = [[n, sz, f"{k}s"] for n, sz, k in held]
                    kind = f"{mount}-missile"
                    for n, sz, k in held:
                        missiles[(sz, k)] = missiles.get((sz, k), 0) + n
                elif any("MissileLauncher" in (p or "") for p in (item.get("payload_types") or [])):
                    max_sz = max(item.get("weapon_sizes") or [0]) or None
                    each = [[1, max_sz, "missile rack"]]
                    kind = f"{mount}-missile"
                elif named:
                    each = [[len(item.get("weapon_sizes") or [1]), named[0][1], named[0][0]]]
                else:
                    continue

            row_key = (who, kind, json.dumps(each))
            entry = rows.setdefault(row_key, [0, each, [0.0, 0.0, 0.0]])
            entry[0] += 1
            totals = [
                item.get("dps_total"),
                item.get("sustained_dps_total"),
                item.get("alpha_total"),
            ]
            fallback_dmg = _damage(guns)
            entry[2] = [
                acc + (val if val is not None else fb)
                for acc, val, fb in zip(entry[2], totals, fallback_dmg, strict=True)
            ]

    role_order = {"pilot": 0, "both": 1, "gunner": 2, "auto": 3}
    guns_rows = [
        [who, mount, count, each, round(dmg[0]), round(dmg[1]), round(dmg[2])]
        for (who, mount, _), (count, each, dmg) in rows.items()
    ]
    guns_rows.sort(
        key=lambda r: (
            role_order.get(r[0], 9),
            "missile" in r[1],
            -max(((g[1] or 0) for g in r[3]), default=0),
        )
    )

    for port in vehicle.get("ports") or []:
        rack = (
            racks.get(port.get("equipped_item_uuid"))
            if port.get("category_label") == "Missile & Bomb Racks"
            else None
        )
        if rack and rack[0]:
            missiles[(rack[1], rack[2])] = missiles.get((rack[1], rack[2]), 0) + rack[0]

    pilot_totals = [
        sum(row[idx] for row in guns_rows if row[0] in ("pilot", "both"))
        for idx in (4, 5, 6)
    ]
    missile_rows = [
        [cnt, sz, kind]
        for (sz, kind), cnt in sorted(missiles.items(), key=lambda x: -(x[0][0] or 0))
    ]
    return {
        "guns": guns_rows,
        "pilot": pilot_totals,
        "missiles": missile_rows,
        "missileCount": (weaponry.get("missiles") or {}).get("count") or 0,
    }


def parse_wiki_price_history(skus: list[dict[str, Any]]) -> list[list[Any]]:
    """
    清洗 Wiki 记录的历史直售与升级商店价格列表（过滤捆绑包与重复项）。

    @param skus: Wiki 载具记录的 skus 数组
    @return: 按日期升序排列的 [[date, usd, is_warbond, store|upgrade, title], ...]
    """
    seen: set[tuple[str, Any, bool, str]] = set()
    rows: list[list[Any]] = []
    for sku in skus:
        title = str(sku.get("title") or "")
        source = sku.get("source")
        if source not in (None, "pledge_store", "upgrade_api") or not sku.get("price"):
            continue
        if sku.get("is_package") or _PACK_RE.search(title) or " upgrade - " in title.lower():
            continue
        wb = sku.get("is_warbond")
        if wb is None:
            wb = "warbond" in title.lower()
        src_kind = "upgrade" if source == "upgrade_api" else "store"
        date_str = str(sku.get("imported_at") or "")[:10]
        row = [date_str, sku["price"], bool(wb), src_kind, title]
        dedupe_key = (date_str, sku["price"], bool(wb), src_kind)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            rows.append(row)
    return sorted(rows, key=lambda r: (r[0], r[1]))


def parse_cargo_grid_file(data: dict[str, Any]) -> CargoGridRecord | None:
    """
    解析单个 sc-cargo.space 货舱 JSON 描述文件为紧凑盒体坐标记录。

    @param data: 解析后的 JSON 字典
    @return: CargoGridRecord 或 None（缺少官方布局时）
    """
    official = data.get("official") or {}
    raw_name = str(data.get("name") or "").strip()
    if not raw_name or not official.get("groups"):
        return None

    boxes: list[list[Any]] = []
    for grp in official["groups"]:
        gx = grp.get("x", 0)
        gz = grp.get("z", 0)
        for box in grp.get("grids") or []:
            boxes.append(
                [
                    gx + box.get("x", 0),
                    gz + box.get("z", 0),
                    box.get("y", 0),
                    box.get("width", 1),
                    box.get("length", 1),
                    box.get("height", 1),
                ]
            )
    scu = float(
        official.get("capacity")
        or sum(float(b[3]) * float(b[4]) * float(b[5]) for b in boxes)
    )
    raw_names = [raw_name] + [str(n) for n in (data.get("alternativeNames") or []) if n]
    alt_keys: list[str] = []
    for n in raw_names:
        plain = re.sub(r"\[[^\]]*\]", "", n)
        stripped = re.sub(r"\([^)]*\)", "", plain)
        for candidate in (normalize_ship_key(plain), normalize_ship_key(stripped)):
            if candidate and candidate not in alt_keys:
                alt_keys.append(candidate)

    return CargoGridRecord(
        key=normalize_ship_key(raw_name),
        name=raw_name,
        alt_keys=alt_keys,
        scu=scu,
        boxes=boxes,
        viewer=f"{CARGO_VIEWER_BASE}{raw_name.lower()}-official",
        concept="concept" in raw_name.lower(),
    )


def find_cargo_grid(
    grids_by_key: dict[str, dict[str, Any]],
    ship_name: str,
) -> dict[str, Any] | None:
    """
    依据船名匹配标准货舱网格模型（优先精准匹配，其次最长前缀遗传）。

    @param grids_by_key: 归一化名称到货舱网格字典的映射
    @param ship_name: 目标舰船英文名
    @return: 货舱网格字典或 None
    """
    key = normalize_ship_key(ship_name)
    if key in grids_by_key:
        return grids_by_key[key]
    starts = [k for k in grids_by_key if key.startswith(f"{k} ")]
    if starts:
        return grids_by_key[max(starts, key=len)]
    longer = {id(grids_by_key[k]): grids_by_key[k] for k in grids_by_key if k.startswith(f"{key} ")}
    if len(longer) == 1 and len(key) >= 5:
        return next(iter(longer.values()))
    return None


class EnrichmentClient:
    """
    外部舰船数据异步富化客户端（Loaners / Wiki / UEX / Cargo Grid）。

    @ivar _net: 全局统一出站网络管理器
    """

    def __init__(self, net: NetworkManager) -> None:
        """
        初始化富化客户端。

        @param net: 已启动的 NetworkManager 实例
        """
        self._net = net

    async def fetch_loaners(self) -> list[LoanerRule]:
        """
        从 RSI Support API 抓取借用船（Loaner Ship Matrix）清单。

        @return: LoanerRule 列表，失败时记录警告并返回空列表
        """
        try:
            resp = await self._net.proxy_client.get(LOANERS_URL, timeout=45.0)
            resp.raise_for_status()
            body = ((resp.json().get("article") or {}).get("body")) or ""
            rules = parse_loaners_html(body)
            logger.info("Fetched %d loaner ship rule(s) from RSI Support", len(rules))
            return rules
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Failed to fetch RSI loaner matrix: %s", exc)
            return []

    async def fetch_wiki_release_and_gold(self, ship_names: list[str]) -> WikiReleaseBundle:
        """
        从 starcitizen.tools Wiki 批量获取实装版本、售卖规律与 Gold Standard 状态。

        @param ship_names: 全量舰船英文名列表
        @return: WikiReleaseBundle 数据对象
        """
        if not ship_names:
            return WikiReleaseBundle()

        released: dict[str, str] = {}
        availability: dict[str, str] = {}
        gold_rows: list[tuple[str, str, str]] = []

        # 1. 每 50 艘一批拉取单船条目页面 Infobox
        try:
            for offset in range(0, len(ship_names), 50):
                batch = ship_names[offset : offset + 50]
                titles = quote("|".join(batch))
                resp = await self._net.proxy_client.get(
                    f"{WIKI_MEDIAWIKI_API}{titles}", timeout=45.0
                )
                resp.raise_for_status()
                query_data = (resp.json().get("query")) or {}
                back_map: dict[str, str] = {}
                for step in ("normalized", "redirects"):
                    for mapping in query_data.get(step) or []:
                        to_title = mapping.get("to") or ""
                        from_title = mapping.get("from") or ""
                        if to_title and from_title:
                            back_map[to_title] = back_map.get(from_title, from_title)
                for page in (query_data.get("pages") or {}).values():
                    revs = page.get("revisions") or []
                    text = (
                        ((revs[0].get("slots") or {}).get("main") or {}).get("*") or ""
                        if revs
                        else ""
                    )
                    title = str(page.get("title") or "")
                    ship_name = back_map.get(title, title)
                    patch = parse_wiki_patch(text)
                    avail = parse_wiki_availability(text)
                    if patch:
                        released[ship_name] = patch
                    if avail:
                        availability[ship_name] = avail
                await asyncio.sleep(0.3)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Failed to fetch Wiki release patches: %s", exc)

        # 2. 拉取 Vehicle_Pipeline 页面提取 Gold Standard 清单
        try:
            resp = await self._net.proxy_client.get(
                f"{WIKI_MEDIAWIKI_API}{WIKI_PIPELINE_PAGE}", timeout=45.0
            )
            resp.raise_for_status()
            pages = ((resp.json().get("query") or {}).get("pages")) or {}
            first_page = next(iter(pages.values()), {})
            revs = first_page.get("revisions") or []
            wikitext = (
                ((revs[0].get("slots") or {}).get("main") or {}).get("*") or "" if revs else ""
            )
            gold_rows = parse_gold_standard_rows(wikitext)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Failed to fetch Wiki Gold Standard list: %s", exc)

        logger.info(
            "Fetched Wiki enrichment (released=%d, availability=%d, gold=%d)",
            len(released),
            len(availability),
            len(gold_rows),
        )
        return WikiReleaseBundle(
            released=released,
            availability=availability,
            gold_rows=gold_rows,
        )

    async def _fetch_wiki_items(self, item_type: str) -> list[dict[str, Any]]:
        """
        分页拉取 Star Citizen Wiki Items 接口全量武器或挂架清单。

        @param item_type: Wiki item 类型名（WeaponGun / MissileLauncher）
        @return: 原始条目字典列表
        """
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            resp = await self._net.proxy_client.get(
                WIKI_ITEMS_URL.format(item_type, page),
                headers={"Accept": "application/json"},
                timeout=45.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            items.extend(payload.get("data") or [])
            last_page = int(((payload.get("meta") or {}).get("last_page")) or 1)
            if page >= last_page:
                return items
            page += 1

    async def fetch_ingame_and_wiki_vehicles(
        self,
        matrix_id_by_key: dict[str, str],
    ) -> dict[str, IngameShipData]:
        """
        从 Star Citizen Wiki API 与 UEX API 拉取游戏内买租价格、保险理赔、武器火力及价格历史。

        @param matrix_id_by_key: {归一化船名: Ship Matrix ID} 映射
        @return: {Ship Matrix ID: IngameShipData} 聚合字典
        """
        try:
            guns_list, racks_list = await asyncio.gather(
                self._fetch_wiki_items("WeaponGun"),
                self._fetch_wiki_items("MissileLauncher"),
            )
            named_sizes: dict[str, set[Any]] = {}
            classes: dict[str, tuple[str, int | None]] = {}
            for g in guns_list:
                g_name = g.get("name")
                if g_name:
                    named_sizes.setdefault(str(g_name), set()).add(g.get("size"))
                    if g.get("class_name"):
                        classes[str(g["class_name"])] = (str(g_name), g.get("size"))
            sizes = {
                n: next(iter(szs)) for n, szs in named_sizes.items() if len(szs) == 1
            }

            racks: dict[str, tuple[int, int | None, str]] = {}
            for rk in racks_list:
                if rk.get("missile_rack"):
                    spec = _extract_rack_spec(rk)
                    if rk.get("uuid"):
                        racks[str(rk["uuid"])] = spec
                    if rk.get("class_name"):
                        racks[str(rk["class_name"])] = spec

            # 分页抓取 Wiki Vehicles（每次保留必要精简字段避免内存飙升）
            wiki_vehicles: list[dict[str, Any]] = []
            page = 1
            while True:
                resp = await self._net.proxy_client.get(
                    WIKI_VEHICLES_URL.format(page),
                    headers={"Accept": "application/json"},
                    timeout=60.0,
                )
                resp.raise_for_status()
                payload = resp.json()
                for veh in payload.get("data") or []:
                    arms = summarize_wiki_weapons(veh, sizes, classes, racks)
                    wiki_vehicles.append(
                        {
                            "id": veh.get("id"),
                            "uuid": veh.get("uuid"),
                            "name": veh.get("name"),
                            "shipmatrix_name": veh.get("shipmatrix_name"),
                            "class_name": veh.get("class_name"),
                            "insurance": veh.get("insurance"),
                            "skus": veh.get("skus"),
                            "version": veh.get("version"),
                            "weapons": arms,
                        }
                    )
                last_page = int(((payload.get("meta") or {}).get("last_page")) or 1)
                if page >= last_page:
                    break
                page += 1
                await asyncio.sleep(0.3)

            # 并发抓取 UEX 载具与游戏内买/租终端价格
            uex_veh_resp, uex_buy_resp, uex_rent_resp = await asyncio.gather(
                self._net.proxy_client.get(f"{UEX_BASE_URL}vehicles", timeout=45.0),
                self._net.proxy_client.get(
                    f"{UEX_BASE_URL}vehicles_purchases_prices_all", timeout=45.0
                ),
                self._net.proxy_client.get(
                    f"{UEX_BASE_URL}vehicles_rentals_prices_all", timeout=45.0
                ),
            )
            for r in (uex_veh_resp, uex_buy_resp, uex_rent_resp):
                r.raise_for_status()
            uex_vehicles = (uex_veh_resp.json().get("data")) or []
            uex_buys = (uex_buy_resp.json().get("data")) or []
            uex_rents = (uex_rent_resp.json().get("data")) or []
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Failed to fetch ingame Wiki/UEX data: %s", exc)
            return {}

        # 按 UEX UUID 及船名索引买/租地点价格
        uex_by_id = {
            v.get("id"): (str(v.get("uuid") or ""), normalize_ship_key(str(v.get("name") or "")))
            for v in uex_vehicles
        }
        prices_index: dict[tuple[tuple[str, str], str], list[list[Any]]] = {}
        for kind, raw_rows, price_field in (
            ("buy", uex_buys, "price_buy"),
            ("rent", uex_rents, "price_rent"),
        ):
            for entry in raw_rows:
                val = entry.get(price_field)
                if not val:
                    continue
                uuid_str, norm_name = uex_by_id.get(
                    entry.get("id_vehicle"),
                    ("", normalize_ship_key(str(entry.get("vehicle_name") or ""))),
                )
                row = [
                    int(round(float(val))),
                    str(entry.get("terminal_name") or ""),
                    int(entry.get("date_modified") or 0),
                ]
                if uuid_str:
                    prices_index.setdefault((("uuid", uuid_str), kind), []).append(row)
                if norm_name:
                    prices_index.setdefault((("name", norm_name), kind), []).append(row)

        # 按 Ship Matrix ID 分组并合并多变体数据
        groups: dict[str, list[dict[str, Any]]] = {}
        for veh in wiki_vehicles:
            mid = veh.get("id") or next(
                (
                    matrix_id_by_key[normalize_ship_key(str(n))]
                    for n in (veh.get("shipmatrix_name"), veh.get("name"))
                    if n and normalize_ship_key(str(n)) in matrix_id_by_key
                ),
                None,
            )
            if mid:
                groups.setdefault(str(mid), []).append(veh)

        results: dict[str, IngameShipData] = {}
        for mid, variants in groups.items():
            main = max(
                variants,
                key=lambda item: (
                    bool((item.get("insurance") or {}).get("claim_time")),
                    len(item.get("skus") or []),
                ),
            )
            ins = main.get("insurance") or {}
            found_prices: dict[str, dict[tuple[int, str], list[Any]]] = {"buy": {}, "rent": {}}
            for var in variants:
                lookup_keys: list[tuple[str, str]] = (
                    [("uuid", str(var["uuid"]))] if var.get("uuid") else []
                )
                lookup_keys.extend(
                    ("name", normalize_ship_key(str(cand)))
                    for cand in (var.get("name"), var.get("shipmatrix_name"))
                    if cand
                )
                for kind in ("buy", "rent"):
                    for lkey in lookup_keys:
                        for row in prices_index.get((lkey, kind), []):
                            found_prices[kind][(row[0], row[1])] = row

            all_skus = [s for var in variants for s in (var.get("skus") or [])]
            class_candidates = [str(v["class_name"]) for v in variants if v.get("class_name")]
            claim_raw = ins.get("claim_time")
            exp_raw = ins.get("expedite_time")
            cost_raw = ins.get("expedite_cost")
            results[mid] = IngameShipData(
                matrix_id=mid,
                game_version=str(main.get("version") or "").split("-")[0],
                class_name=min(class_candidates, key=len) if class_candidates else "",
                claim_time_min=round(float(claim_raw), 1) if claim_raw else None,
                expedite_time_min=round(float(exp_raw), 1) if exp_raw else None,
                expedite_cost_auec=int(round(float(cost_raw))) if cost_raw else None,
                buy_locations=sorted(found_prices["buy"].values(), key=lambda x: (x[0], x[1])),
                rent_locations=sorted(found_prices["rent"].values(), key=lambda x: (x[0], x[1])),
                weapons_summary=main.get("weapons"),
                price_history=parse_wiki_price_history(all_skus),
            )

        logger.info("Aggregated ingame & price history data for %d ship(s)", len(results))
        return results

    async def fetch_cargo_grids(
        self,
        known_sha: str = "",
    ) -> tuple[str, dict[str, dict[str, Any]] | None]:
        """
        从 sc-cargo.space GitHub 仓库拉取各舰船货舱布局。

        若树 SHA 未变更则返回 (known_sha, None) 跳过重复下载。

        @param known_sha: 上次成功缓存的 GitHub Git Tree SHA
        @return: (最新 tree_sha, {归一化名称或别名: grid_dict} 或 None)
        """
        try:
            tree_resp = await self._net.proxy_client.get(CARGO_TREE_URL, timeout=30.0)
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()
            sha = str(tree_data.get("sha") or "")
            if sha and sha == known_sha:
                return sha, None

            grid_paths = [
                str(entry["path"])
                for entry in (tree_data.get("tree") or [])
                if str(entry.get("path") or "").startswith("src/grids/")
                and str(entry.get("path") or "").endswith(".json")
            ]

            sem = asyncio.Semaphore(8)

            async def _pull_one(rel_path: str) -> CargoGridRecord | None:
                async with sem:
                    r = await self._net.proxy_client.get(
                        f"{CARGO_RAW_BASE}{rel_path}", timeout=20.0
                    )
                    r.raise_for_status()
                    return parse_cargo_grid_file(r.json())

            records = await asyncio.gather(
                *(_pull_one(p) for p in grid_paths), return_exceptions=True
            )
            valid = [rec for rec in records if isinstance(rec, CargoGridRecord)]
            # 正式版本优先覆盖概念版本布局
            valid.sort(key=lambda rec: rec.concept)
            grids_by_key: dict[str, dict[str, Any]] = {}
            for rec in valid:
                grid_dict = {
                    "scu": rec.scu,
                    "boxes": rec.boxes,
                    "viewer": rec.viewer,
                    "concept": rec.concept,
                }
                for key_alias in [rec.key, *rec.alt_keys]:
                    if key_alias:
                        grids_by_key.setdefault(key_alias, grid_dict)

            logger.info("Fetched %d cargo grid layout(s) from sc-cargo.space", len(valid))
            return sha, grids_by_key
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Failed to fetch sc-cargo.space grids: %s", exc)
            return known_sha, None
