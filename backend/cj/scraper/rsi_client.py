"""
RSI 公开商店、升级（CCU）商店与 Ship Matrix 异步抓取客户端。

基于上游 pledgewatch 的 GraphQL 与访客 Cookie 认证流重构为全异步 httpx 实现，
所有网络请求统一经由 cj.core.net.NetworkManager 发出并自动继承环境变量出口代理。
"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from cj.core.logging import get_logger
from cj.core.net import NetworkManager

logger = get_logger(__name__)

RSI_BASE_URL = "https://robertsspaceindustries.com"
STORE_PAGE_URL = f"{RSI_BASE_URL}/en/pledge/ships"
STORE_GRAPHQL_URL = f"{RSI_BASE_URL}/graphql"
UPGRADE_GRAPHQL_URL = f"{RSI_BASE_URL}/pledge-store/api/upgrade/graphql"
UPGRADE_PAGE_URL = f"{RSI_BASE_URL}/en/account/pledges"
SHIP_MATRIX_URL = f"{RSI_BASE_URL}/ship-matrix/index"
STANDALONE_SHIP_CATEGORY = "72"
ADDONS_CATEGORY = "3"

BROWSE_LISTING_QUERY = """query GetBrowseListingQuery($query: SearchQuery) {
  store(browse: true) {
    listing: search(query: $query) {
      resources {
        id name url
        media { thumbnail { storeSmall } }
        ... on TyItem {
          price { amount discounted }
          nativePrice { amount discounted }
          stock { available show level unlimited qty backOrder }
        }
        ... on TySku { isWarbond gameItems { name kind ... on ShipGameItem { shipId } } }
      }
      totalCount
    }
  }
}"""

INIT_UPGRADE_WITH_EXPIRY_QUERY = """query initShipUpgrade {
  ships {
    id name msrp medias { productThumbMediumAndSmall }
    skus { id title price available validUntil }
  }
}"""

INIT_UPGRADE_BASE_QUERY = """query initShipUpgrade {
  ships {
    id name msrp medias { productThumbMediumAndSmall }
    skus { id title price available }
  }
}"""

FILTER_ACTIVE_TO_SKUS_QUERY = """query filterShips(
  $fromId: Int,
  $toFilters: [FilterConstraintValues]
) {
  to(from: $fromId, filters: $toFilters) {
    ships {
      id
      skus { id price available }
    }
  }
}"""

FILTER_FROM_COSTS_QUERY = """query filterShips(
  $fromId: Int,
  $toFilters: [FilterConstraintValues]
) {
  to(from: $fromId, filters: $toFilters) {
    ships {
      id
      skus { id upgradePrice }
    }
  }
}"""


class RsiScrapeError(RuntimeError):
    """
    RSI 公开接口抓取或认证流程失败时抛出的领域异常。
    """


@dataclass(frozen=True, slots=True)
class ScrapedShipMatrixItem:
    """
    来自 RSI Ship Matrix 的全量舰船目录条目。

    @ivar matrix_id: Ship Matrix 舰船 ID
    @ivar name: 规范化英文舰船名
    @ivar key: 归一化检索键名
    @ivar manufacturer: 制造商名称
    @ivar focus: 舰船定位
    @ivar size: 舰船尺寸等级
    @ivar production_status: 生产状态（flight-ready/in-concept/in-production）
    @ivar image_url: 缩略图完整地址
    @ivar store_url: 商店详情页完整地址
    @ivar manufacturer_code: 制造商简称代码（如 ANVL/RSI）
    @ivar ship_type: 舰船大类（如 Combat/Transport）
    @ivar min_crew: 最小乘员人数
    @ivar max_crew: 最大乘员人数
    @ivar cargo_scu: 标准货舱容积（SCU）
    @ivar length_m: 船体长度（米）
    """

    matrix_id: str
    name: str
    key: str
    manufacturer: str
    focus: str
    size: str
    production_status: str
    image_url: str
    store_url: str
    manufacturer_code: str = ""
    ship_type: str = ""
    min_crew: int | None = None
    max_crew: int | None = None
    cargo_scu: float | None = None
    length_m: float | None = None


@dataclass(frozen=True, slots=True)
class ScrapedStandaloneShip:
    """
    来自 RSI 单船商店与 Add-Ons 模块商店的在售状态快照。

    @ivar sku_id: 单船商店 SKU ID
    @ivar matrix_id: 关联的 Ship Matrix ID（多船包或独立模块为空）
    @ivar name: 商品标题
    @ivar key: 去除版本后缀后的归一化船名键
    @ivar price_cents: 当前售价（美分）
    @ivar available: 是否有货
    @ivar limited: 是否限量版
    @ivar is_warbond: 是否战争债券（Warbond）版本
    @ivar store_url: 商品链接
    @ivar image_url: 商品缩略图链接
    @ivar category: 商品类别（ship 为单船版本，module 为舰船模块）
    @ivar edition_label: 版本标签（Standard/Warbond/10 Year/Best in Show）
    @ivar list_price_cents: 划线标价（美分，用于判断单船直降折扣）
    @ivar stock_level: 库存提示文案（如 Low）
    @ivar insurance: 随附保险标签（Lifetime/10 years/6 months 等）
    @ivar extras: 随附涂装/挂载/模块名列表
    """

    sku_id: str
    matrix_id: str
    name: str
    key: str
    price_cents: int
    available: bool
    limited: bool
    is_warbond: bool
    store_url: str
    image_url: str
    category: str = "ship"
    edition_label: str = "Standard"
    list_price_cents: int | None = None
    stock_level: str = ""
    insurance: str = ""
    extras: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScrapedUpgradeSku:
    """
    单个可购买的 CCU SKU 数据结构。

    @ivar sku_id: RSI 升级 SKU ID
    @ivar title: 升级版本标题（如 Standard Edition / Warbond Edition）
    @ivar edition_label: 清洗后的版本标签
    @ivar is_warbond: 是否属于 Warbond 优惠升级
    @ivar price_cents: 当前升级目标购买价（美分）
    @ivar expires_at: 到期 UTC 时间；RSI 未返回时为 None
    """

    sku_id: int
    title: str
    edition_label: str
    is_warbond: bool
    price_cents: int
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ScrapedUpgradeShip:
    """
    升级商店中的目标舰船及其当前在售 CCU SKU 集合。

    @ivar upgrade_ship_id: RSI 升级接口分配的舰船 ID
    @ivar name: 舰船英文名称
    @ivar key: 归一化舰船键名
    @ivar msrp_cents: 舰船原始价值 MSRP（美分）
    @ivar image_url: 舰船缩略图地址
    @ivar skus: 该舰船当前开放购买的 CCU SKU 列表
    """

    upgrade_ship_id: int
    name: str
    key: str
    msrp_cents: int
    image_url: str
    skus: list[ScrapedUpgradeSku]


def normalize_ship_key(name: str) -> str:
    """
    将舰船名称归一化为仅包含小写字母、数字和单空格的标准键名。

    @param name: 原始舰船名称
    @return: 归一化键名
    """
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def base_ship_key(name: str) -> str:
    """
    剥离商店商品标题尾部常见的版本后缀后再进行归一化，便于与 Ship Matrix 匹配。

    @param name: 商店商品原始标题
    @return: 剥离版本后缀后的归一化船名键
    """
    stripped = re.sub(
        r"\s+-\s+((?:\d+\s+Year|IAE|ILW|Invictus|Warbond|Best\s+In\s+Show|Standard).*)$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    return normalize_ship_key(stripped)


def to_absolute_url(url: str | None) -> str:
    """
    将 RSI 站内相对路径补全为完整 HTTPS 地址。

    @param url: 原始链接路径
    @return: 补全后的绝对 URL 或空字符串
    """
    cleaned = (url or "").strip()
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        return f"{RSI_BASE_URL}{cleaned}"
    return cleaned


def clean_upgrade_edition_label(title: str) -> str:
    """
    清洗升级版本标题，提取精简的展示标签（如 Warbond、BIS Warbond、Best in Show、Standard）。

    @param title: RSI 返回的 SKU 原始标题
    @return: 清洗后的简短版本标签
    """
    if re.search(r"best\s+in\s+show", title, re.IGNORECASE):
        if re.search(r"\bwarbond\b", title, re.IGNORECASE):
            return "BIS Warbond"
        return "Best in Show"
    cleaned = re.sub(r"\s*Edition\b", "", title, flags=re.IGNORECASE).strip()
    return cleaned or "Standard"


def parse_insurance_label(text: str) -> str:
    """
    将 RSI 商店返回的保险条目文本格式化为统一简短标签。

    @param text: 原始保险描述文本（如 120 Month Insurance / Lifetime Insurance）
    @return: 标准化保险期限字符串（Lifetime / 10 years / 6 months 等）
    """
    lowered = text.lower()
    if "lifetime" in lowered:
        return "Lifetime"
    if "120 month" in lowered:
        return "10 years"
    if "6 month" in lowered:
        return "6 months"
    matched = re.search(r"(\d+)\s*month", lowered)
    if matched:
        months = int(matched.group(1))
        if months % 12 == 0 and months >= 24:
            return f"{months // 12} years"
        return f"{months} months"
    return text.replace(" Insurance", "").strip()


def parse_standalone_edition_label(name: str, url: str, is_warbond: bool) -> str:
    """
    识别单船商店 SKU 所属的版本分类（Warbond / 10 Year / Best in Show / Standard）。

    @param name: 商品标题
    @param url: 商品链接
    @param is_warbond: GraphQL 返回的 isWarbond 布尔标志
    @return: 标准版本分类名称
    """
    if is_warbond or re.search(r"\bwarbond\b", name, re.IGNORECASE):
        return "Warbond"
    normalized_url = url.replace("-", " ")
    for label, pattern in (
        ("10 Year", re.compile(r"\b10 year\b", re.IGNORECASE)),
        ("Best in Show", re.compile(r"best in show", re.IGNORECASE)),
    ):
        if pattern.search(name) or pattern.search(normalized_url):
            return label
    return "Standard"


def extract_module_labels(extras: list[str]) -> list[str]:
    """
    从套装附带物品名称列表中提取舰船模块简称（如 Galaxy - Cargo Module -> Cargo）。

    @param extras: SKU Extras 列表
    @return: 模块名称列表
    """
    result: list[str] = []
    for item in extras:
        tail = item.split(" - ")[-1].strip()
        matched = re.search(r"([\w ]+?)\s+Module$", tail, re.IGNORECASE)
        if matched:
            result.append(matched.group(1).strip())
    return result


def _parse_opt_float(raw: Any) -> float | None:
    """
    安全将数值字段转换为浮点数，空值或非法值返回 None。

    @param raw: 原始字段值
    @return: 浮点数或 None
    """
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_opt_int(raw: Any) -> int | None:
    """
    安全将数值字段转换为整数，空值或非法值返回 None。

    @param raw: 原始字段值
    @return: 整数或 None
    """
    val = _parse_opt_float(raw)
    return int(round(val)) if val is not None else None


def parse_rsi_datetime(raw_value: Any) -> datetime | None:
    """
    将 RSI 接口可能返回的 ISO-8601 时间字符串或 Unix 时间戳转换为 UTC 时间对象。

    @param raw_value: 原始时间值
    @return: 带 UTC 时区的 datetime 对象，无法解析或为空时返回 None
    """
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, (int, float)):
        return datetime.fromtimestamp(float(raw_value), tz=UTC)
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            return None
    return None


def _is_buyable_sku(title: str) -> bool:
    """
    过滤仅限订阅者（Subscriber）购买的升级 SKU，避免对普通访客产生误报。

    @param title: SKU 标题
    @return: 若普通玩家可买返回 True，否则返回 False
    """
    return "subscriber" not in title.lower()


async def _bootstrap_rsi_guest_token(client: httpx.AsyncClient) -> str:
    """
    访问 RSI 公开商店页面获取访客 Rsi-Token Cookie。

    @param client: 独立 Cookie 容器的异步 HTTP 客户端
    @return: 提取到的 Rsi-Token 字符串
    @raise RsiScrapeError: 网络异常或未获取到 Rsi-Token 时抛出
    """
    try:
        response = await client.get(STORE_PAGE_URL)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RsiScrapeError(f"Failed to load RSI store page: {exc}") from exc

    token = client.cookies.get("Rsi-Token")
    if not token:
        raise RsiScrapeError("RSI store page did not return Rsi-Token cookie")
    return token


class RsiScraperClient:
    """
    RSI 公开数据源异步抓取客户端。

    @ivar _net: 全局统一网络管理器实例
    @ivar _upgrade_expiry_supported: 升级商店 GraphQL Sku 是否支持 validUntil 扩展字段
    """

    def __init__(self, net: NetworkManager) -> None:
        """
        初始化 RSI 抓取客户端。

        @param net: 已启动的 NetworkManager 实例
        """
        self._net = net
        self._upgrade_expiry_supported = True

    async def fetch_ship_matrix(self) -> list[ScrapedShipMatrixItem]:
        """
        拉取 RSI 官方 Ship Matrix 全量舰船清单。

        @return: ScrapedShipMatrixItem 列表
        @raise RsiScrapeError: 网络异常或响应结构不符合预期时抛出
        """
        try:
            response = await self._net.proxy_client.get(SHIP_MATRIX_URL, timeout=60.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RsiScrapeError(f"Failed to fetch RSI Ship Matrix: {exc}") from exc

        raw_ships = payload.get("data")
        if not isinstance(raw_ships, list):
            raise RsiScrapeError("RSI Ship Matrix response missing list 'data'")

        items: list[ScrapedShipMatrixItem] = []
        for entry in raw_ships:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = " ".join(str(entry["name"]).split())
            maker = entry.get("manufacturer") or {}
            media_list = entry.get("media") or [{}]
            first_media = media_list[0] if isinstance(media_list, list) and media_list else {}
            images = first_media.get("images") or {}
            raw_url = str(entry.get("url") or "")
            items.append(
                ScrapedShipMatrixItem(
                    matrix_id=str(entry.get("id") or ""),
                    name=name,
                    key=normalize_ship_key(name),
                    manufacturer=str(maker.get("name") or ""),
                    manufacturer_code=str(maker.get("code") or ""),
                    focus=str(entry.get("focus") or ""),
                    ship_type=str(entry.get("type") or ""),
                    size=str(entry.get("size") or ""),
                    min_crew=_parse_opt_int(entry.get("min_crew")),
                    max_crew=_parse_opt_int(entry.get("max_crew")),
                    cargo_scu=_parse_opt_float(entry.get("cargocapacity")),
                    length_m=_parse_opt_float(entry.get("length")),
                    production_status=str(entry.get("production_status") or ""),
                    image_url=to_absolute_url(images.get("store_small")),
                    store_url=to_absolute_url(raw_url),
                )
            )
        logger.info("Fetched %d ships from RSI Ship Matrix", len(items))
        return items

    async def _query_store_category(
        self,
        client: httpx.AsyncClient,
        token: str,
        product_category: str,
    ) -> list[dict[str, Any]]:
        """
        分页拉取指定商品分类的 RSI 公开商店资源列表。

        @param client: 携带 Rsi-Token 的 HTTP 客户端
        @param token: 访客 Rsi-Token
        @param product_category: 分类 ID（72 为单船，3 为 Add-Ons）
        @return: 原始 GraphQL 资源字典列表
        @raise RsiScrapeError: 网络请求或 GraphQL 返回错误时抛出
        """
        resources: list[dict[str, Any]] = []
        page = 1
        page_size = 100

        while True:
            body = [
                {
                    "operationName": "GetBrowseListingQuery",
                    "query": BROWSE_LISTING_QUERY,
                    "variables": {
                        "query": {
                            "skus": {"products": [product_category]},
                            "limit": page_size,
                            "page": page,
                        }
                    },
                }
            ]
            try:
                resp = await client.post(
                    STORE_GRAPHQL_URL,
                    json=body,
                    headers={"x-rsi-token": token},
                )
                resp.raise_for_status()
                reply = resp.json()[0]
            except (httpx.HTTPError, ValueError, IndexError) as exc:
                raise RsiScrapeError(
                    f"Failed to query standalone store listing: {exc}"
                ) from exc

            if reply.get("errors"):
                msgs = "; ".join(str(e.get("message", "?")) for e in reply["errors"])
                raise RsiScrapeError(f"RSI standalone GraphQL returned errors: {msgs}")

            listing = ((reply.get("data") or {}).get("store") or {}).get("listing") or {}
            batch = listing.get("resources") or []
            total_count = int(listing.get("totalCount") or 0)
            resources.extend(batch)
            if len(resources) >= total_count or not batch:
                break
            page += 1
        return resources

    def _parse_store_resource(
        self,
        resource: dict[str, Any],
        category: str,
    ) -> ScrapedStandaloneShip:
        """
        将 RSI 商店单个 SKU 资源解析为 ScrapedStandaloneShip 结构。

        @param resource: GraphQL 返回的单个 SKU 资源对象
        @param category: 商品类别（ship 或 module）
        @return: ScrapedStandaloneShip 数据对象
        """
        price = resource.get("price") or {}
        stock = resource.get("stock") or {}
        game_items = resource.get("gameItems") or []
        ship_ids = [
            str(g["shipId"])
            for g in game_items
            if isinstance(g, dict) and g.get("kind") == "SHIP" and g.get("shipId")
        ]
        insurance_label = next(
            (
                parse_insurance_label(str(g["name"]))
                for g in game_items
                if isinstance(g, dict) and g.get("kind") == "INSURANCE" and g.get("name")
            ),
            "",
        )
        extras = [
            str(g["name"]).strip()
            for g in game_items
            if isinstance(g, dict)
            and g.get("kind") in ("SKIN", "CUSTOMIZATION", "HANGARPASS", "SHIPMOD")
            and g.get("name")
        ]
        raw_url = str(resource.get("url") or "")
        store_url = f"{RSI_BASE_URL}/en{raw_url}" if raw_url.startswith("/") else raw_url
        # 优先使用 nativePrice（税前 USD 美分），避免地区税费导致单船及
        # 10 Year/Warbond 价格虚高且非 5 的倍数
        native_price = resource.get("nativePrice") or {}
        native_list = native_price.get("amount")
        native_discounted = native_price.get("discounted")
        list_amount = (
            native_list
            if native_list is not None and int(native_list) > 0
            else price.get("amount")
        )
        amount = (
            native_discounted
            if native_discounted is not None and int(native_discounted) > 0
            else (
                native_list
                if native_list is not None and int(native_list) > 0
                else (price.get("discounted") or price.get("amount") or 0)
            )
        )
        name = " ".join(str(resource.get("name") or "").split())
        is_wb = bool(resource.get("isWarbond"))

        return ScrapedStandaloneShip(
            sku_id=str(resource["id"]),
            matrix_id=ship_ids[0] if len(ship_ids) == 1 else "",
            name=name,
            key=base_ship_key(name),
            price_cents=int(amount),
            available=bool(stock.get("available", True)),
            limited=not bool(stock.get("unlimited", True)),
            is_warbond=is_wb,
            store_url=store_url,
            image_url=to_absolute_url(
                ((resource.get("media") or {}).get("thumbnail") or {}).get("storeSmall")
            ),
            category=category,
            edition_label=parse_standalone_edition_label(name, store_url, is_wb),
            list_price_cents=int(list_amount) if list_amount else None,
            stock_level=str(stock.get("level") or ""),
            insurance=insurance_label,
            extras=extras,
        )

    async def fetch_standalone_store(self) -> list[ScrapedStandaloneShip]:
        """
        拉取 RSI 公开商店当前在售单船及独立模块清单。

        @return: ScrapedStandaloneShip 列表
        @raise RsiScrapeError: GraphQL 请求或认证失败时抛出
        """
        async with self._net.new_rsi_cookie_client(timeout_seconds=30.0) as client:
            token = await _bootstrap_rsi_guest_token(client)
            ship_resources = await self._query_store_category(
                client, token, STANDALONE_SHIP_CATEGORY
            )
            results = [self._parse_store_resource(r, "ship") for r in ship_resources]

            # Add-Ons 模块属于非核心附加信息，拉取异常时仅记录 warning，不阻断单船检查
            try:
                addon_resources = await self._query_store_category(client, token, ADDONS_CATEGORY)
                for res in addon_resources:
                    kinds = {
                        g.get("kind")
                        for g in (res.get("gameItems") or [])
                        if isinstance(g, dict)
                    }
                    title = str(res.get("name") or "")
                    if "SHIP" not in kinds and re.search(r"\bmodule\b", title, re.IGNORECASE):
                        results.append(self._parse_store_resource(res, "module"))
            except RsiScrapeError as exc:
                logger.warning("Failed to fetch optional ship modules from add-ons: %s", exc)

        logger.info("Fetched %d standalone store SKUs from RSI store", len(results))
        return results

    @asynccontextmanager
    async def open_upgrade_session(
        self, timeout_seconds: float = 15.0
    ) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
        """
        建立可复用的访客升级会话上下文管理器，便于批量遍历起始船升级可达图。

        @param timeout_seconds: 单次请求超时时间（秒）
        @return: 产出 (httpx.AsyncClient, headers) 二元组的异步上下文管理器
        """
        async with self._net.new_rsi_cookie_client(timeout_seconds=timeout_seconds) as client:
            headers = await self.establish_upgrade_headers(client)
            yield client, headers

    async def establish_upgrade_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        """
        使用独立异步 Cookie 客户端完成 RSI 升级工具访客两步握手认证。

        @param client: 已分配独立 CookieJar 的异步 HTTP 客户端
        @return: 包含 x-rsi-token 的请求头字典
        @raise RsiScrapeError: 获取令牌或握手遭拒绝时抛出
        """
        token = await _bootstrap_rsi_guest_token(client)
        headers = {"x-rsi-token": token}
        for path in ("/api/account/v2/setAuthToken", "/api/ship-upgrades/setContextToken"):
            try:
                resp = await client.post(f"{RSI_BASE_URL}{path}", json={}, headers=headers)
                resp.raise_for_status()
                handshake = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise RsiScrapeError(f"Upgrade handshake failed at {path}: {exc}") from exc
            if not handshake.get("success"):
                raise RsiScrapeError(f"Upgrade handshake refused at {path}")
        return headers

    async def query_upgrade_costs_with_client(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        from_upgrade_ship_id: int,
    ) -> dict[int, int]:
        """
        在已握手的访客升级会话上查询指定起始船当前可直接购买的所有目标 CCU SKU 及差价。

        @param client: 已完成升级握手的 httpx.AsyncClient
        @param headers: 包含 x-rsi-token 的请求头
        @param from_upgrade_ship_id: 起始底船在 RSI 升级工具中的 ID
        @return: {目标 CCU SKU ID: 差价美分} 字典
        @raise RsiScrapeError: 请求或 GraphQL 查询出错时抛出
        """
        body = [
            {
                "operationName": "filterShips",
                "query": FILTER_FROM_COSTS_QUERY,
                "variables": {"fromId": from_upgrade_ship_id, "toFilters": []},
            }
        ]
        try:
            resp = await client.post(UPGRADE_GRAPHQL_URL, json=body, headers=headers)
            resp.raise_for_status()
            reply = resp.json()[0]
        except (httpx.HTTPError, ValueError, IndexError) as exc:
            raise RsiScrapeError(
                f"Failed to query upgrade costs from ship {from_upgrade_ship_id}: {exc}"
            ) from exc

        if reply.get("errors"):
            err_msg = "; ".join(str(e.get("message", "?")) for e in reply["errors"])
            raise RsiScrapeError(f"RSI filterShips GraphQL returned errors: {err_msg}")

        to_ships = (((reply.get("data") or {}).get("to")) or {}).get("ships") or []
        if not isinstance(to_ships, list):
            raise RsiScrapeError("RSI filterShips GraphQL returned invalid 'to.ships' payload")

        costs: dict[int, int] = {}
        for ship in to_ships:
            if not isinstance(ship, dict):
                continue
            for sku in ship.get("skus") or []:
                if not isinstance(sku, dict) or sku.get("id") is None:
                    continue
                upgrade_price = sku.get("upgradePrice")
                if upgrade_price:
                    costs[int(sku["id"])] = int(upgrade_price)
        return costs

    async def fetch_upgrade_costs_from(self, from_upgrade_ship_id: int) -> dict[int, int]:
        """
        单次建立独立升级会话，查询某艘起始船当前全部可用 CCU 及实时补差价。

        @param from_upgrade_ship_id: 起始船的 RSI upgrade ship id
        @return: {SKU ID: 差价美分} 映射字典
        @raise RsiScrapeError: 升级会话建立或查询失败时抛出
        """
        async with self._net.new_rsi_cookie_client(timeout_seconds=15.0) as client:
            headers = await self.establish_upgrade_headers(client)
            return await self.query_upgrade_costs_with_client(
                client, headers, from_upgrade_ship_id
            )

    async def fetch_upgrade_store(self) -> list[ScrapedUpgradeShip]:
        """
        建立访客升级会话并拉取当前全量可升级目标舰船与在售 CCU SKU 清单。

        优先尝试查询 SKU 过期时间字段；若上游 GraphQL Schema 不支持该字段则自动回退至标准查询。

        @return: ScrapedUpgradeShip 列表
        @raise RsiScrapeError: 升级会话握手失败或 GraphQL 查询出错时抛出
        """
        async with self._net.new_rsi_cookie_client(timeout_seconds=30.0) as client:
            headers = await self.establish_upgrade_headers(client)

            # 2. 查询全量舰船估值与 SKU 元数据，同时查询当前实际在售的 To SKU 集合
            raw_ships = await self._query_upgrade_ships_with_fallback(client, headers)
            active_sku_prices = await self._query_active_to_skus(client, headers)

        parsed_ships: list[ScrapedUpgradeShip] = []
        for entry in raw_ships:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            name = " ".join(str(entry["name"]).split())
            msrp_cents = int(entry.get("msrp") or 0)
            image_url = to_absolute_url(
                (entry.get("medias") or {}).get("productThumbMediumAndSmall")
            )

            skus: list[ScrapedUpgradeSku] = []
            for raw_sku in entry.get("skus") or []:
                if not isinstance(raw_sku, dict):
                    continue
                sku_id = int(raw_sku["id"])
                if sku_id not in active_sku_prices:
                    continue
                if not raw_sku.get("available", True):
                    continue
                title = " ".join(str(raw_sku.get("title") or "").split())
                if not title or not _is_buyable_sku(title):
                    continue
                # initShipUpgrade 的 raw_sku["price"] 恒为税前 USD，优于可能含税的 filterShips
                price_cents = int(raw_sku.get("price") or active_sku_prices[sku_id] or 0)
                if price_cents <= 0:
                    continue
                expires_raw = (
                    raw_sku.get("validUntil")
                    or raw_sku.get("valid_until")
                    or raw_sku.get("endDate")
                    or raw_sku.get("end_date")
                )
                skus.append(
                    ScrapedUpgradeSku(
                        sku_id=sku_id,
                        title=title,
                        edition_label=clean_upgrade_edition_label(title),
                        is_warbond=bool(re.search(r"\bwarbond\b", title, re.IGNORECASE)),
                        price_cents=price_cents,
                        expires_at=parse_rsi_datetime(expires_raw),
                    )
                )

            parsed_ships.append(
                ScrapedUpgradeShip(
                    upgrade_ship_id=int(entry["id"]),
                    name=name,
                    key=normalize_ship_key(name),
                    msrp_cents=msrp_cents,
                    image_url=image_url,
                    skus=skus,
                )
            )
        logger.info(
            "Fetched %d upgrade ships (%d buyable SKUs) from RSI upgrade store",
            len(parsed_ships),
            sum(len(s.skus) for s in parsed_ships),
        )
        return parsed_ships

    async def _query_upgrade_ships_with_fallback(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        """
        优先请求包含 validUntil 的升级查询；若 GraphQL Schema 校验拒绝则自动降级为标准查询。

        @param client: 已携带升级会话 Cookie 的异步 HTTP 客户端
        @param headers: 包含 x-rsi-token 的请求头
        @return: RSI GraphQL 返回的原始 ships 字典列表
        @raise RsiScrapeError: 当网络请求或标准回退查询仍失败时抛出
        """
        queries = (
            (INIT_UPGRADE_WITH_EXPIRY_QUERY, INIT_UPGRADE_BASE_QUERY)
            if self._upgrade_expiry_supported
            else (INIT_UPGRADE_BASE_QUERY,)
        )
        for query_str in queries:
            is_extended = query_str == INIT_UPGRADE_WITH_EXPIRY_QUERY
            body = [{"operationName": "initShipUpgrade", "query": query_str, "variables": {}}]
            try:
                resp = await client.post(UPGRADE_GRAPHQL_URL, json=body, headers=headers)
                if is_extended and resp.status_code == 400:
                    self._upgrade_expiry_supported = False
                    logger.debug(
                        "Upgrade GraphQL returned HTTP 400 for extended expiry field; "
                        "falling back to base query"
                    )
                    continue
                resp.raise_for_status()
                reply = resp.json()[0]
            except (httpx.HTTPError, ValueError, IndexError) as exc:
                raise RsiScrapeError(f"Failed to query upgrade GraphQL: {exc}") from exc

            if reply.get("errors"):
                err_msg = "; ".join(str(e.get("message", "?")) for e in reply["errors"])
                if is_extended:
                    self._upgrade_expiry_supported = False
                    logger.debug(
                        "Upgrade GraphQL rejected extended expiry field (%s); retrying base query",
                        err_msg,
                    )
                    continue
                raise RsiScrapeError(f"RSI upgrade GraphQL returned errors: {err_msg}")

            ships = ((reply.get("data") or {}).get("ships")) or []
            if isinstance(ships, list):
                return ships
            raise RsiScrapeError("RSI upgrade GraphQL returned invalid 'ships' payload")

        return []

    async def _query_active_to_skus(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
    ) -> dict[int, int | None]:
        """
        调用 filterShips(fromId=None) 获取当前真实在售的升级目标 SKU 集合与价格。

        initShipUpgrade 会返回历史全部已存在过的 SKU（包括已停售旧版），
        仅 filterShips 返回的 to.ships.skus 代表当前商店实际可购的在售 CCU。

        @param client: 已携带升级会话 Cookie 的异步 HTTP 客户端
        @param headers: 包含 x-rsi-token 的请求头
        @return: {在售 SKU ID: 实时价格美分或 None} 映射字典
        @raise RsiScrapeError: 网络请求或 GraphQL 返回错误时抛出
        """
        body = [
            {
                "operationName": "filterShips",
                "query": FILTER_ACTIVE_TO_SKUS_QUERY,
                "variables": {"fromId": None, "toFilters": []},
            }
        ]
        try:
            resp = await client.post(UPGRADE_GRAPHQL_URL, json=body, headers=headers)
            resp.raise_for_status()
            reply = resp.json()[0]
        except (httpx.HTTPError, ValueError, IndexError) as exc:
            raise RsiScrapeError(f"Failed to query active To SKUs via filterShips: {exc}") from exc

        if reply.get("errors"):
            err_msg = "; ".join(str(e.get("message", "?")) for e in reply["errors"])
            raise RsiScrapeError(f"RSI filterShips GraphQL returned errors: {err_msg}")

        to_ships = (((reply.get("data") or {}).get("to")) or {}).get("ships") or []
        if not isinstance(to_ships, list):
            raise RsiScrapeError("RSI filterShips GraphQL returned invalid 'to.ships' payload")

        active_skus: dict[int, int | None] = {}
        for ship in to_ships:
            if not isinstance(ship, dict):
                continue
            for sku in ship.get("skus") or []:
                if not isinstance(sku, dict) or not sku.get("available", True):
                    continue
                if sku.get("id") is None:
                    continue
                raw_price = sku.get("price")
                active_skus[int(sku["id"])] = int(raw_price) if raw_price else None
        return active_skus
