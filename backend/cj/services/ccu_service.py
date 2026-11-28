"""
CCU 监控、Diff 检测与最近可升级起始船计算服务模块。

负责编排 RSI 商店与 Ship Matrix 抓取、全量状态对比、Changelog 落库、
最近可升级起始船（nearest_from_ships）差价计算，并在事务提交成功后发布领域事件。
"""

import asyncio
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cj.core.config import Settings
from cj.core.events import EventBus, EventType
from cj.core.logging import get_logger
from cj.db.models import CcuSku, Changelog, Ship, StoreSku, ensure_utc, utc_now
from cj.repositories.ccu_repo import CcuRepository
from cj.repositories.changelog_repo import ChangelogRepository
from cj.repositories.ship_repo import ShipRepository
from cj.repositories.store_repo import StoreRepository
from cj.scraper.enrichment_client import (
    EnrichmentClient,
    fill_variant_values,
    find_cargo_grid,
    match_loaner_for_key,
    resolve_gold_for_ship,
)
from cj.scraper.rsi_client import (
    UPGRADE_PAGE_URL,
    RsiScrapeError,
    RsiScraperClient,
    ScrapedShipMatrixItem,
    ScrapedStandaloneShip,
    ScrapedUpgradeShip,
    extract_module_labels,
    normalize_ship_key,
    to_absolute_url,
)
from cj.services.i18n_service import I18nService, build_search_index

logger = get_logger(__name__)

MIN_LISTED_SHARE = 0.5
MISSES_BEFORE_DELISTED = 2
COSTS_CACHE_TTL_SECONDS = 600
PLAN_MIN_COVERAGE_SHARE = 0.8
PRICE_INCREMENT_CENTS = 500
BATCH_TAX_SHIFT_MIN_COUNT = 8
BATCH_TAX_SHIFT_RATIO_SHARE = 0.7
BATCH_NON_STEP_SHARE = 0.3
SHIP_STATUS_LABELS = {
    "flight-ready": "Flight ready",
    "in-concept": "In concept",
    "in-production": "In production",
}
EDITION_SORT_ORDER = {"Standard": 0, "Warbond": 1, "10 Year": 2, "Best in Show": 3}
MODULE_GRID_ALIASES = {"med bay": "medical"}


def is_standard_usd_price(price_cents: int | None) -> bool:
    """
    校验 RSI 商店税前 USD 价格是否为合法的 $5 整倍数（500 美分增量）。

    @param price_cents: 价格（美分）或 None
    @return: 若为大于 0 且整除 500 的价格返回 True
    """
    return (
        price_cents is not None
        and price_cents > 0
        and price_cents % PRICE_INCREMENT_CENTS == 0
    )


def is_abnormal_price_change(
    old_price_cents: int | None,
    new_price_cents: int | None,
) -> bool:
    """
    判断单条价格变动是否属于非正常税率/币种异常波动。

    @param old_price_cents: 变更前价格（美分）
    @param new_price_cents: 变更后价格（美分）
    @return: 任一侧不符合 $5 整数倍规范时返回 True
    """
    return not (is_standard_usd_price(old_price_cents) and is_standard_usd_price(new_price_cents))


def is_abnormal_price_change_batch(pairs: Sequence[tuple[int, int]]) -> bool:
    """
    判断同批次多条价格变动是否属于全站税率/币种纠偏导致的批量异常刷新。

    当同批变动不少于 8 条且满足以下任一条件时判定为异常批次：
    1. 至少 30% 的条目包含非 $5 倍数价格；
    2. 至少 70% 的条目以同一非 1.0 比例（1% 精度）同步涨跌。

    @param pairs: (old_price_cents, new_price_cents) 元组序列
    @return: 判定为异常批次时返回 True
    """
    valid_pairs = [(old_p, new_p) for old_p, new_p in pairs if old_p > 0 and new_p > 0]
    if len(valid_pairs) < BATCH_TAX_SHIFT_MIN_COUNT:
        return False

    non_step = sum(1 for old_p, new_p in valid_pairs if is_abnormal_price_change(old_p, new_p))
    if (non_step / len(valid_pairs)) >= BATCH_NON_STEP_SHARE:
        return True

    ratio_counts = Counter(round((old_p / new_p) * 100) for old_p, new_p in valid_pairs)
    dominant_ratio, dominant_count = ratio_counts.most_common(1)[0]
    return dominant_ratio != 100 and (dominant_count / len(valid_pairs)) >= (
        BATCH_TAX_SHIFT_RATIO_SHARE
    )


def _is_special_sale_edition(
    edition_label: str,
    *,
    limited: bool = False,
    title: str = "",
) -> bool:
    """
    判断版本或商品标题是否属于特殊销售（Best in Show、限量波次等）。

    @param edition_label: 版本展示标签
    @param limited: 是否为限量库存发售
    @param title: 原始商品或 SKU 标题
    @return: 若属于特殊销售返回 True
    """
    lowered = f"{edition_label} {title}".lower()
    words = re.findall(r"[a-z]+", lowered)
    return bool(limited or "best in show" in lowered or "bis" in words)


@dataclass(frozen=True, slots=True)
class ShipCandidate:
    """
    用于计算最近可升级起始船的轻量不可变舰船候选值对象。

    @ivar name_en: 英文船名
    @ivar name_zh: 中文船名
    @ivar value_cents: 舰船当前升级估值（美分）
    @ivar always_available: 是否在商店常驻无限制可买
    """

    name_en: str
    name_zh: str
    value_cents: int
    always_available: bool


def find_nearest_from_ships(
    target_price_cents: int,
    candidate_ships: Sequence[ShipCandidate],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    计算距离目标 CCU 实际购买价最近的可用起始底船列表（纯函数核心算法）。

    筛选所有价值严格小于 target_price_cents 的舰船并按价值降序排列，同时保障结果中
    尽可能包含常驻可买底船以便玩家直接购买起点，精确计算每艘船需补差价。

    @param target_price_cents: 目标 CCU 实际购买价格（美分，WBCCU 取优惠价，普通 CCU 取原价）
    @param candidate_ships: 当前全量候选舰船列表
    @param limit: 最多返回的起始船数量，默认 5 艘
    @return: 按价值降序排列的起始船字典列表
    @raise ValueError: 当 target_price_cents 小于等于 0 时抛出
    """
    # 1. 校验目标价格合法性
    if target_price_cents <= 0:
        raise ValueError("目标价格必须大于 0")
    if limit <= 0:
        return []

    # 2. 筛选严格低于目标价的有效船只并按价值降序、常驻优先排序
    eligible = [ship for ship in candidate_ships if 0 < ship.value_cents < target_price_cents]
    if not eligible:
        return []

    eligible.sort(key=lambda s: (-s.value_cents, not s.always_available, s.name_en.lower()))
    selected = eligible[:limit]

    # 3. 若前 limit 项中没有常驻可买船，则将末位替换为距离最近的常驻底船
    if not any(item.always_available for item in selected):
        always_pool = [s for s in eligible[limit:] if s.always_available]
        if always_pool:
            selected[-1] = always_pool[0]

    # 4. 组装标准输出结构并计算每艘船的美元估值与补差价
    return [
        {
            "name_en": ship.name_en,
            "name_zh": ship.name_zh or ship.name_en,
            "value": round(ship.value_cents / 100, 2),
            "upgrade_cost": round((target_price_cents - ship.value_cents) / 100, 2),
            "always_available": ship.always_available,
        }
        for ship in selected
    ]


def _format_iso_utc(dt: datetime | None) -> str | None:
    """
    将时间对象格式化为标准 UTC ISO-8601 字符串（末尾带 Z）。

    @param dt: 待格式化时间或 None
    @return: ISO 字符串或 None
    """
    normalized = ensure_utc(dt)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


class CcuService:
    """
    CCU 监控与全量商店聚合业务服务。

    @ivar _session_factory: 异步会话工厂
    @ivar _ship_repo: 舰船仓库
    @ivar _ccu_repo: CCU SKU 仓库
    @ivar _changelog_repo: 变动日志仓库
    @ivar _scraper: RSI 异步抓取客户端
    @ivar _i18n_service: 舰船中文化服务
    @ivar _event_bus: 进程内异步事件总线
    @ivar _store_repo: 单船商店与升级路线仓库
    @ivar _enrichment: 第三方数据富化客户端（Wiki / UEX / sc-cargo）
    @ivar _settings: 可选静态配置实例
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        ship_repo: ShipRepository,
        ccu_repo: CcuRepository,
        changelog_repo: ChangelogRepository,
        scraper: RsiScraperClient,
        i18n_service: I18nService,
        event_bus: EventBus,
        store_repo: StoreRepository | None = None,
        enrichment: EnrichmentClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        """
        初始化 CCU 监控与商店聚合业务服务。

        @param session_factory: 异步会话工厂
        @param ship_repo: 舰船 Repository
        @param ccu_repo: CCU Repository
        @param changelog_repo: 变动日志 Repository
        @param scraper: RSI 抓取客户端
        @param i18n_service: 汉化服务
        @param event_bus: 进程内事件总线
        @param store_repo: 可选单船商店及路径 Repository
        @param enrichment: 可选外部富化客户端
        @param settings: 可选应用静态配置实例
        """
        self._session_factory = session_factory
        self._ship_repo = ship_repo
        self._ccu_repo = ccu_repo
        self._changelog_repo = changelog_repo
        self._scraper = scraper
        self._i18n_service = i18n_service
        self._event_bus = event_bus
        self._store_repo = store_repo or StoreRepository()
        self._enrichment = enrichment
        self._settings = settings
        self._costs_cache: dict[int, tuple[float, dict[str, int]]] = {}
        self._costs_locks: dict[int, asyncio.Lock] = {}

    def _cargo_cache_path(self) -> Path | None:
        """
        获取货舱网格本地 JSON 缓存文件路径（若未传入 settings 则返回 None）。

        @return: Path 实例或 None
        """
        if self._settings is None:
            return None
        return self._settings.data_dir / "cargo_grids_cache.json"

    def _match_matrix_key(self, matrix_keys: set[str], item: ScrapedStandaloneShip) -> str:
        """
        将商店商品名称映射至最匹配的 Ship Matrix 归一化键（含前缀模糊降级）。

        @param matrix_keys: 已知 Ship Matrix 舰船键集合
        @param item: 抓取到的单船商店商品条目
        @return: 匹配到的键名或商品自身 key
        """
        if item.key in matrix_keys:
            return item.key
        starts = [k for k in matrix_keys if item.key.startswith(f"{k} ")]
        if starts:
            return max(starts, key=len)
        return item.key

    async def _apply_standalone_store_diff(
        self,
        session: AsyncSession,
        standalone_items: list[ScrapedStandaloneShip],
        all_ships: list[Ship],
        now: datetime,
        events_out: list[tuple[EventType, str, list[str], dict[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """
        对比并持久化单船商店 SKU 与 Add-Ons 模块快照，生成变动日志并刷新舰船常驻标志。

        同时针对批量税率纠偏与非 $5 倍数异常价格变化进行过滤，仅将正常的 Warbond 整船与
        特殊销售上架/补货/降价事件暂存至 events_out，待事务提交后统一派发至事件总线。

        @param session: 当前活跃数据库事务会话
        @param standalone_items: 最新抓取的单船与模块列表
        @param all_ships: 当前会话内全部舰船实体列表
        @param now: 当前 UTC 时间
        @param events_out: 可选的提交后通知事件收集列表
        @return: 状态统计字典
        """
        ships_by_matrix = {s.rsi_matrix_id: s for s in all_ships if s.rsi_matrix_id}
        ships_by_key = {s.key: s for s in all_ships}
        matrix_keys = set(ships_by_key)

        existing_rows = await self._store_repo.list_all_store_skus(session)
        existing_by_id = {row.sku_id: row for row in existing_rows}
        known_ship_listed = sum(
            1 for row in existing_rows if row.listed and row.category == "ship"
        )
        ship_items = [item for item in standalone_items if item.category == "ship"]
        if known_ship_listed > 0 and len(ship_items) < known_ship_listed * MIN_LISTED_SHARE:
            logger.warning(
                "Standalone store returned %d ship SKUs (expected ~%d); skipping diff",
                len(ship_items),
                known_ship_listed,
            )
            return {"seen": len(standalone_items), "skipped": 1}

        is_baseline = len(existing_rows) == 0
        seen_by_id = {item.sku_id: item for item in standalone_items}

        # 统计各船当前单船在售和常驻状态
        in_store_ship_ids: set[int] = set()
        always_ship_ids: set[int] = set()
        in_store_keys: set[str] = set()
        always_keys: set[str] = set()
        pending_price_changes: list[tuple[ScrapedStandaloneShip, Ship | None, int]] = []

        for item in standalone_items:
            matched_ship: Ship | None = None
            if item.category == "ship":
                if item.matrix_id and item.matrix_id in ships_by_matrix:
                    matched_ship = ships_by_matrix[item.matrix_id]
                else:
                    mk = self._match_matrix_key(matrix_keys, item)
                    matched_ship = ships_by_key.get(mk)
                    if matched_ship is None and item.key:
                        clean_name = re.sub(r"\s+-\s+.*$", "", item.name).strip() or item.name
                        matched_ship = Ship(
                            key=item.key,
                            rsi_matrix_id=item.matrix_id,
                            name_en=clean_name,
                            name_zh="",
                            auto_translated=False,
                            search_index=build_search_index(clean_name, ""),
                            value_cents=item.price_cents if not item.is_warbond else 0,
                            always_available=False,
                            in_store=True,
                            store_url=item.store_url,
                            image_url=item.image_url,
                            updated_at=now,
                        )
                        await self._ship_repo.add(session, matched_ship)
                        all_ships.append(matched_ship)
                        ships_by_key[matched_ship.key] = matched_ship
                        matrix_keys.add(matched_ship.key)

                if matched_ship is not None:
                    if not matched_ship.image_url and item.image_url:
                        matched_ship.image_url = item.image_url
                    if not matched_ship.store_url and item.store_url:
                        matched_ship.store_url = item.store_url
                    if item.available:
                        in_store_ship_ids.add(matched_ship.id)
                        in_store_keys.add(matched_ship.key)
                    if item.available and not item.limited and not item.is_warbond:
                        always_ship_ids.add(matched_ship.id)
                        always_keys.add(matched_ship.key)
            else:
                # Add-Ons 独立模块：匹配名称前部所属的模块化舰船
                head = re.split(r"\bmodule\b", item.name, maxsplit=1, flags=re.IGNORECASE)[0]
                norm_head = normalize_ship_key(head)
                best_k = max(
                    (k for k in matrix_keys if norm_head.startswith(f"{k} ")),
                    key=len,
                    default=None,
                )
                if best_k:
                    matched_ship = ships_by_key.get(best_k)

            old_row = existing_by_id.get(item.sku_id)
            ship_id = matched_ship.id if matched_ship else None
            if old_row is None:
                new_row = StoreSku(
                    sku_id=item.sku_id,
                    ship_id=ship_id,
                    rsi_matrix_id=item.matrix_id,
                    category=item.category,
                    raw_name=item.name,
                    edition_label=item.edition_label,
                    price_cents=item.price_cents,
                    list_price_cents=item.list_price_cents,
                    is_warbond=item.is_warbond,
                    available=item.available,
                    limited=item.limited,
                    stock_level=item.stock_level,
                    insurance=item.insurance,
                    extras=list(item.extras),
                    store_url=item.store_url,
                    image_url=item.image_url,
                    listed=True,
                    misses=0,
                    first_seen_at=now,
                    last_seen_at=now,
                    listed_since_at=now,
                )
                await self._store_repo.add_store_sku(session, new_row)
                if not is_baseline and item.category == "ship":
                    self._add_store_changelog(
                        session=session,
                        event_type="store.new",
                        item=item,
                        ship=matched_ship,
                        old_price_cents=None,
                        now=now,
                    )
                    self._queue_standalone_event(
                        events_out=events_out,
                        item=item,
                        ship=matched_ship,
                        store_action="store.new",
                        old_price_cents=None,
                        now=now,
                    )
                continue

            was_listed = old_row.listed
            was_available = old_row.available
            old_price = old_row.price_cents

            old_row.ship_id = ship_id
            old_row.rsi_matrix_id = item.matrix_id
            old_row.category = item.category
            old_row.raw_name = item.name
            old_row.edition_label = item.edition_label
            old_row.price_cents = item.price_cents
            old_row.list_price_cents = item.list_price_cents
            old_row.is_warbond = item.is_warbond
            old_row.available = item.available
            old_row.limited = item.limited
            old_row.stock_level = item.stock_level
            old_row.insurance = item.insurance
            old_row.extras = list(item.extras)
            old_row.store_url = item.store_url
            old_row.image_url = item.image_url
            old_row.listed = True
            old_row.misses = 0
            old_row.last_seen_at = now

            if item.category == "ship" and not is_baseline:
                if not was_listed:
                    old_row.listed_since_at = now
                    self._add_store_changelog(
                        session=session,
                        event_type="store.back",
                        item=item,
                        ship=matched_ship,
                        old_price_cents=old_price,
                        now=now,
                    )
                    self._queue_standalone_event(
                        events_out=events_out,
                        item=item,
                        ship=matched_ship,
                        store_action="store.back",
                        old_price_cents=old_price,
                        now=now,
                    )
                elif was_available and not item.available:
                    self._add_store_changelog(
                        session=session,
                        event_type="store.sold_out",
                        item=item,
                        ship=matched_ship,
                        old_price_cents=old_price,
                        now=now,
                    )
                elif not was_available and item.available:
                    self._add_store_changelog(
                        session=session,
                        event_type="store.restock",
                        item=item,
                        ship=matched_ship,
                        old_price_cents=old_price,
                        now=now,
                    )
                    self._queue_standalone_event(
                        events_out=events_out,
                        item=item,
                        ship=matched_ship,
                        store_action="store.restock",
                        old_price_cents=old_price,
                        now=now,
                    )
                elif old_price and item.price_cents and item.price_cents != old_price:
                    pending_price_changes.append((item, matched_ship, old_price))

        # 对价格变动执行批量税率异常与单条 $5 校验防护
        if pending_price_changes:
            price_pairs = [(old_p, it.price_cents) for it, _, old_p in pending_price_changes]
            if is_abnormal_price_change_batch(price_pairs):
                logger.warning(
                    "Suppressed abnormal standalone price-change batch (count=%d)",
                    len(pending_price_changes),
                )
            else:
                for it, matched_ship, old_p in pending_price_changes:
                    if is_abnormal_price_change(old_p, it.price_cents):
                        logger.debug(
                            "Skipped abnormal standalone price change sku=%s (%s -> %s)",
                            it.sku_id,
                            old_p,
                            it.price_cents,
                        )
                        continue
                    self._add_store_changelog(
                        session=session,
                        event_type="store.price_changed",
                        item=it,
                        ship=matched_ship,
                        old_price_cents=old_p,
                        now=now,
                    )
                    if it.price_cents < old_p:
                        self._queue_standalone_event(
                            events_out=events_out,
                            item=it,
                            ship=matched_ship,
                            store_action="store.price_changed",
                            old_price_cents=old_p,
                            now=now,
                        )

        # 标记离店单船及模块
        ships_by_id = {s.id: s for s in all_ships}
        for row in existing_rows:
            if row.sku_id in seen_by_id or not row.listed:
                continue
            row.misses += 1
            if row.misses >= MISSES_BEFORE_DELISTED:
                row.listed = False
                if row.category == "ship" and not is_baseline:
                    ship_ref = ships_by_id.get(row.ship_id) if row.ship_id else None
                    session.add(
                        Changelog(
                            occurred_at=now,
                            event_type="store.gone",
                            sku_id=0,
                            ship_name_en=ship_ref.name_en if ship_ref else row.raw_name,
                            ship_name_zh=ship_ref.name_zh if ship_ref else "",
                            edition_label=row.edition_label,
                            is_warbond=row.is_warbond,
                            old_price_cents=row.price_cents,
                            new_price_cents=None,
                            original_price_cents=row.list_price_cents or row.price_cents,
                            details={"url": row.store_url, "raw_name": row.raw_name},
                        )
                    )

        # 同步所有船只的 in_store 与 always_available 标志
        for ship in all_ships:
            ship.in_store = (ship.id in in_store_ship_ids) or (ship.key in in_store_keys)
            ship.always_available = (
                ship.id in always_ship_ids
                or ship.key in always_keys
                or self._i18n_service.is_default_always_available(ship.key)
            )

        return {"seen": len(standalone_items), "skipped": 0}

    def _add_store_changelog(
        self,
        session: AsyncSession,
        event_type: str,
        item: ScrapedStandaloneShip,
        ship: Ship | None,
        old_price_cents: int | None,
        now: datetime,
    ) -> None:
        """
        将单船商店上下架与价格波动事件写入历史变动日志表。

        @param session: 异步数据库事务会话
        @param event_type: 事件类型名称
        @param item: 单船商品快照
        @param ship: 关联舰船实体或 None
        @param old_price_cents: 历史价格（美分）或 None
        @param now: 发生时间 UTC
        """
        session.add(
            Changelog(
                occurred_at=now,
                event_type=event_type,
                sku_id=0,
                ship_name_en=ship.name_en if ship else item.name,
                ship_name_zh=ship.name_zh if ship else "",
                edition_label=item.edition_label,
                is_warbond=item.is_warbond,
                old_price_cents=old_price_cents,
                new_price_cents=item.price_cents,
                original_price_cents=item.list_price_cents or item.price_cents,
                details={
                    "store_sku_id": item.sku_id,
                    "url": item.store_url,
                    "limited": item.limited,
                    "raw_name": item.name,
                },
            )
        )

    def _queue_standalone_event(
        self,
        events_out: list[tuple[EventType, str, list[str], dict[str, Any]]] | None,
        item: ScrapedStandaloneShip,
        ship: Ship | None,
        store_action: str,
        old_price_cents: int | None,
        now: datetime,
    ) -> None:
        """
        为单船 Warbond 整船或特殊销售（Best in Show / 限量）构建并暂存异步通知事件。

        @param events_out: 事务提交后待派发事件列表（为 None 时跳过）
        @param item: 单船商店 SKU 快照
        @param ship: 关联舰船实体或 None
        @param store_action: 变动动作标识（store.new/store.back/store.restock/store.price_changed）
        @param old_price_cents: 历史价格（美分）或 None
        @param now: 当前 UTC 时间
        """
        if events_out is None or not item.available:
            return
        is_special = _is_special_sale_edition(
            item.edition_label, limited=item.limited, title=item.name
        )
        if not item.is_warbond and not is_special:
            return

        orig_cents = (
            item.list_price_cents
            or (ship.value_cents if ship and ship.value_cents >= item.price_cents else None)
            or item.price_cents
        )
        savings_cents = max(0, orig_cents - item.price_cents)
        if item.is_warbond:
            webhook_event = "store.warbond_ship"
            sale_type = "warbond_ship"
            event_tags = ["store.warbond_ship"]
            if is_special:
                event_tags.append("store.special_sale")
        else:
            webhook_event = "store.special_sale"
            sale_type = "special_sale"
            event_tags = ["store.special_sale"]

        bus_event = (
            EventType.STORE_PRICE_CHANGED
            if store_action == "store.price_changed"
            else EventType.STORE_LISTED
        )
        ship_id = (
            str(ship.rsi_upgrade_id or ship.id)
            if ship is not None
            else (item.matrix_id or item.sku_id)
        )
        raw_image = item.image_url or (ship.image_url if ship else "")
        raw_url = item.store_url or (ship.store_url if ship else "")
        payload = {
            "event": webhook_event,
            "timestamp": _format_iso_utc(now),
            "data": {
                "ship_id": ship_id,
                "sku_id": item.sku_id,
                "ship_name_en": ship.name_en if ship else item.name,
                "ship_name_zh": ship.name_zh if ship else "",
                "edition_label": item.edition_label,
                "sale_type": sale_type,
                "store_action": store_action,
                "is_warbond": item.is_warbond,
                "is_special_sale": is_special,
                "original_price": round(orig_cents / 100, 2),
                "discounted_price": round(item.price_cents / 100, 2),
                "previous_price": (
                    round(old_price_cents / 100, 2) if old_price_cents is not None else None
                ),
                "savings": round(savings_cents / 100, 2),
                "currency": "USD",
                "expires_at": None,
                "store_url": to_absolute_url(raw_url) or UPGRADE_PAGE_URL,
                "image_url": to_absolute_url(raw_image),
                "nearest_from_ships": [],
            },
        }
        events_out.append((bus_event, webhook_event, event_tags, payload))

    async def sync_standalone_store(
        self,
        standalone_items: list[ScrapedStandaloneShip] | None = None,
    ) -> dict[str, int]:
        """
        轻量同步 RSI 单船及模块在售状态并记录门店变动历史（供高频巡检任务使用）。

        @param standalone_items: 可选的预抓取单船数据（测试注入用）
        @return: 同步汇总字典
        """
        try:
            if standalone_items is None:
                standalone_items = await self._scraper.fetch_standalone_store()
        except RsiScrapeError as exc:
            logger.warning("Standalone store scrape skipped: %s", exc)
            return {"seen": 0, "skipped": 1}

        now = utc_now()
        events_to_publish: list[tuple[EventType, str, list[str], dict[str, Any]]] = []
        async with self._session_factory() as session:
            async with session.begin():
                all_ships = await self._ship_repo.list_all(session)
                stats = await self._apply_standalone_store_diff(
                    session, standalone_items, all_ships, now, events_out=events_to_publish
                )

        for bus_event, webhook_event, event_tags, payload in events_to_publish:
            await self._event_bus.publish(
                bus_event,
                {
                    "webhook_event": webhook_event,
                    "event_tags": event_tags,
                    "payload": payload,
                },
            )
        return stats

    async def sync_catalog_and_standalone(
        self,
        matrix_items: list[ScrapedShipMatrixItem] | None = None,
        standalone_items: list[ScrapedStandaloneShip] | None = None,
    ) -> int:
        """
        同步 RSI Ship Matrix 目录与单船在售状态，更新常驻底船标记与中英检索索引。

        @param matrix_items: 可选的预抓取 Ship Matrix 数据（用于测试注入）
        @param standalone_items: 可选的预抓取单船商店数据（用于测试注入）
        @return: 本次处理或更新的舰船条目数
        """
        try:
            if matrix_items is None:
                matrix_items = await self._scraper.fetch_ship_matrix()
            if standalone_items is None:
                standalone_items = await self._scraper.fetch_standalone_store()
        except RsiScrapeError as exc:
            logger.warning("Catalog/standalone sync aborted: %s", exc)
            return 0

        # 1. 预先汇总单船商店中的价格、在售状态与常驻标识
        standalone_by_matrix: dict[str, ScrapedStandaloneShip] = {}
        standalone_by_key: dict[str, ScrapedStandaloneShip] = {}
        always_keys: set[str] = set()
        in_store_keys: set[str] = set()

        for item in standalone_items:
            if item.matrix_id:
                standalone_by_matrix[item.matrix_id] = item
            if item.key:
                standalone_by_key.setdefault(item.key, item)
                if item.available:
                    in_store_keys.add(item.key)
                if item.available and not item.limited and not item.is_warbond:
                    always_keys.add(item.key)

        # 2. 预先计算缺失中文译名（含可能的 AI 外部请求，避免占用数据库事务）
        now = utc_now()
        pending_translations: dict[str, tuple[str, bool]] = {}
        for m in matrix_items:
            res = await self._i18n_service.resolve_translation(m.name)
            pending_translations[m.key] = (res.name_zh, res.auto_translated)

        # 3. 显式事务批量入库 Ship Matrix 与单船商店 SKU 快照
        processed = 0
        events_to_publish: list[tuple[EventType, str, list[str], dict[str, Any]]] = []
        async with self._session_factory() as session:
            async with session.begin():
                existing_ships = await self._ship_repo.list_all(session)
                by_key = {s.key: s for s in existing_ships}

                for item in matrix_items:
                    if not item.key:
                        continue
                    zh_name, auto_flag = pending_translations.get(item.key, ("", False))
                    store_match = standalone_by_matrix.get(item.matrix_id) or standalone_by_key.get(
                        item.key
                    )
                    is_always = (
                        item.key in always_keys
                        or self._i18n_service.is_default_always_available(item.key)
                    )
                    in_store = item.key in in_store_keys
                    store_url = store_match.store_url if store_match else item.store_url
                    image_url = item.image_url or (store_match.image_url if store_match else "")

                    ship = by_key.get(item.key)
                    if ship is None:
                        fallback_price = (
                            store_match.price_cents
                            if store_match and not store_match.is_warbond
                            else 0
                        )
                        maker_zh = self._i18n_service.resolve_manufacturer_zh(
                            item.manufacturer, item.manufacturer_code
                        )
                        ship = Ship(
                            key=item.key,
                            rsi_matrix_id=item.matrix_id,
                            name_en=item.name,
                            name_zh=zh_name,
                            auto_translated=auto_flag,
                            search_index=build_search_index(
                                item.name, zh_name, item.manufacturer, maker_zh
                            ),
                            manufacturer=item.manufacturer,
                            manufacturer_code=item.manufacturer_code,
                            focus=item.focus,
                            ship_type=item.ship_type,
                            size=item.size,
                            min_crew=item.min_crew,
                            max_crew=item.max_crew,
                            cargo_scu=item.cargo_scu,
                            length_m=item.length_m,
                            production_status=item.production_status,
                            value_cents=fallback_price,
                            always_available=is_always,
                            in_store=in_store,
                            store_url=store_url,
                            image_url=image_url,
                            updated_at=now,
                        )
                        await self._ship_repo.add(session, ship)
                        existing_ships.append(ship)
                        by_key[item.key] = ship
                    else:
                        ship.rsi_matrix_id = item.matrix_id or ship.rsi_matrix_id
                        ship.manufacturer = item.manufacturer or ship.manufacturer
                        ship.manufacturer_code = item.manufacturer_code or ship.manufacturer_code
                        ship.focus = item.focus or ship.focus
                        ship.ship_type = item.ship_type or ship.ship_type
                        ship.size = item.size or ship.size
                        if item.min_crew is not None:
                            ship.min_crew = item.min_crew
                        if item.max_crew is not None:
                            ship.max_crew = item.max_crew
                        if item.cargo_scu is not None:
                            ship.cargo_scu = item.cargo_scu
                        if item.length_m is not None:
                            ship.length_m = item.length_m
                        ship.production_status = item.production_status or ship.production_status
                        if zh_name:
                            ship.name_zh = zh_name
                            ship.auto_translated = auto_flag
                        elif ship.class_name:
                            cls_res = self._i18n_service.resolve_local(
                                ship.name_en, class_name=ship.class_name
                            )
                            if cls_res.name_zh:
                                ship.name_zh = cls_res.name_zh
                                ship.auto_translated = cls_res.auto_translated
                        maker_zh = self._i18n_service.resolve_manufacturer_zh(
                            ship.manufacturer, ship.manufacturer_code
                        )
                        ship.search_index = build_search_index(
                            ship.name_en, ship.name_zh, ship.manufacturer, maker_zh
                        )
                        ship.always_available = is_always
                        ship.in_store = in_store
                        if store_url:
                            ship.store_url = store_url
                        if image_url:
                            ship.image_url = image_url
                        ship.updated_at = now
                    processed += 1

                await self._apply_standalone_store_diff(
                    session,
                    standalone_items,
                    existing_ships,
                    now,
                    events_out=events_to_publish,
                )

        for bus_event, webhook_event, event_tags, payload in events_to_publish:
            await self._event_bus.publish(
                bus_event,
                {
                    "webhook_event": webhook_event,
                    "event_tags": event_tags,
                    "payload": payload,
                },
            )

        logger.info("Catalog/standalone sync completed (processed=%d)", processed)
        return processed

    async def sync_and_diff_ccus(
        self,
        upgrade_ships: list[ScrapedUpgradeShip] | None = None,
    ) -> dict[str, Any]:
        """
        抓取 RSI 升级商店并与历史快照对比 Diff，事务提交后再通过 EventBus 发布新上架/调价事件。

        @param upgrade_ships: 可选的预抓取升级商店数据（用于单元测试注入）
        @return: 包含状态、新增/调价/下架计数及已发布事件数的汇总字典
        """
        # 1. 拉取升级商店数据（失败时不破坏已有数据）
        try:
            if upgrade_ships is None:
                upgrade_ships = await self._scraper.fetch_upgrade_store()
        except RsiScrapeError as exc:
            logger.warning("CCU scrape failed; skipping diff cycle: %s", exc)
            return {"status": "error", "reason": str(exc), "published_events": 0}

        seen_sku_map: dict[int, tuple[ScrapedUpgradeShip, Any]] = {
            sku.sku_id: (ship, sku) for ship in upgrade_ships for sku in ship.skus
        }

        # 2. 在事务开始前预先解析各船译名，防止 AI 外部请求拖慢数据库锁
        pre_translations: dict[str, tuple[str, bool]] = {}
        for u_ship in upgrade_ships:
            res = await self._i18n_service.resolve_translation(u_ship.name)
            pre_translations[u_ship.key] = (res.name_zh, res.auto_translated)

        now = utc_now()
        events_to_publish: list[tuple[EventType, str, list[str], dict[str, Any]]] = []
        listed_count = 0
        relisted_count = 0
        price_changed_count = 0
        delisted_count = 0
        is_baseline = False

        # 3. 显式事务：保护校验、舰船 upsert、SKU Diff 与 Changelog 落库
        async with self._session_factory() as session:
            async with session.begin():
                existing_skus = {sku.sku_id: sku for sku in await self._ccu_repo.list_all(session)}
                known_listed_count = sum(1 for s in existing_skus.values() if s.listed)

                if (
                    known_listed_count > 0
                    and len(seen_sku_map) < known_listed_count * MIN_LISTED_SHARE
                ):
                    logger.warning(
                        "Upgrade store returned %d SKUs (expected around %d); skipping bad read",
                        len(seen_sku_map),
                        known_listed_count,
                    )
                    return {
                        "status": "skipped",
                        "seen_skus": len(seen_sku_map),
                        "expected_min": int(known_listed_count * MIN_LISTED_SHARE),
                        "published_events": 0,
                    }

                is_baseline = len(existing_skus) == 0

                # 3a. 同步舰船主表估值与 RSI upgrade_id 映射
                all_ships = await self._ship_repo.list_all(session)
                ships_by_upgrade_id = {
                    s.rsi_upgrade_id: s for s in all_ships if s.rsi_upgrade_id is not None
                }
                ships_by_key = {s.key: s for s in all_ships}
                ship_by_upgrade_id: dict[int, Ship] = {}

                for u_ship in upgrade_ships:
                    ship = ships_by_upgrade_id.get(u_ship.upgrade_ship_id) or ships_by_key.get(
                        u_ship.key
                    )
                    zh_name, auto_flag = pre_translations.get(u_ship.key, ("", False))
                    if ship is None:
                        ship = Ship(
                            key=u_ship.key,
                            rsi_upgrade_id=u_ship.upgrade_ship_id,
                            name_en=u_ship.name,
                            name_zh=zh_name,
                            auto_translated=auto_flag,
                            search_index=build_search_index(u_ship.name, zh_name),
                            value_cents=u_ship.msrp_cents,
                            always_available=self._i18n_service.is_default_always_available(
                                u_ship.key
                            ),
                            in_store=False,
                            store_url=UPGRADE_PAGE_URL,
                            image_url=u_ship.image_url,
                            updated_at=now,
                        )
                        await self._ship_repo.add(session, ship)
                        all_ships.append(ship)
                        ships_by_key[ship.key] = ship
                    else:
                        ship.rsi_upgrade_id = u_ship.upgrade_ship_id
                        if u_ship.msrp_cents > 0:
                            ship.value_cents = u_ship.msrp_cents
                        if not ship.name_zh and zh_name:
                            ship.name_zh = zh_name
                            ship.auto_translated = auto_flag
                        ship.search_index = build_search_index(
                            ship.name_en, ship.name_zh, ship.manufacturer
                        )
                        if not ship.image_url and u_ship.image_url:
                            ship.image_url = u_ship.image_url
                        ship.updated_at = now
                    ships_by_upgrade_id[u_ship.upgrade_ship_id] = ship
                    ship_by_upgrade_id[u_ship.upgrade_ship_id] = ship

                # 构建内存候选底船池以便在事物内即算出 nearest_from_ships
                candidate_pool = [
                    ShipCandidate(
                        name_en=s.name_en,
                        name_zh=s.name_zh,
                        value_cents=s.value_cents,
                        always_available=s.always_available,
                    )
                    for s in all_ships
                    if s.value_cents > 0
                ]

                # 3b. 对比本次在售 SKU 变化
                pending_ccu_price_changes: list[tuple[Ship, int, Any, int, int, str]] = []
                for sku_id, (u_ship, sku_data) in seen_sku_map.items():
                    ship = ship_by_upgrade_id[u_ship.upgrade_ship_id]
                    orig_cents = (
                        u_ship.msrp_cents
                        if u_ship.msrp_cents >= sku_data.price_cents
                        else sku_data.price_cents
                    )
                    old_sku = existing_skus.get(sku_id)

                    if old_sku is None:
                        new_row = CcuSku(
                            sku_id=sku_id,
                            ship_id=ship.id,
                            rsi_upgrade_ship_id=u_ship.upgrade_ship_id,
                            title=sku_data.title,
                            edition_label=sku_data.edition_label,
                            is_warbond=sku_data.is_warbond,
                            original_price_cents=orig_cents,
                            price_cents=sku_data.price_cents,
                            listed=True,
                            misses=0,
                            expires_at=sku_data.expires_at,
                            store_url=ship.store_url or UPGRADE_PAGE_URL,
                            first_seen_at=now,
                            last_seen_at=now,
                            listed_since_at=now,
                        )
                        await self._ccu_repo.add(session, new_row)
                        if not is_baseline:
                            listed_count += 1
                            events_to_publish.append(
                                self._record_change_and_build_event(
                                    session=session,
                                    bus_event=EventType.CCU_LISTED,
                                    webhook_event="ccu.new_listing",
                                    changelog_event="ccu.listed",
                                    ship=ship,
                                    sku_id=sku_id,
                                    edition_label=sku_data.edition_label,
                                    is_warbond=sku_data.is_warbond,
                                    old_price_cents=None,
                                    new_price_cents=sku_data.price_cents,
                                    original_price_cents=orig_cents,
                                    expires_at=sku_data.expires_at,
                                    store_url=new_row.store_url,
                                    candidates=candidate_pool,
                                    now=now,
                                )
                            )
                        continue

                    was_listed = old_sku.listed
                    old_price = old_sku.price_cents

                    old_sku.ship_id = ship.id
                    old_sku.rsi_upgrade_ship_id = u_ship.upgrade_ship_id
                    old_sku.title = sku_data.title
                    old_sku.edition_label = sku_data.edition_label
                    old_sku.is_warbond = sku_data.is_warbond
                    old_sku.original_price_cents = orig_cents
                    old_sku.price_cents = sku_data.price_cents
                    old_sku.listed = True
                    old_sku.misses = 0
                    old_sku.expires_at = sku_data.expires_at
                    old_sku.last_seen_at = now
                    if not was_listed:
                        old_sku.listed_since_at = now
                        relisted_count += 1
                        events_to_publish.append(
                            self._record_change_and_build_event(
                                session=session,
                                bus_event=EventType.CCU_LISTED,
                                webhook_event="ccu.new_listing",
                                changelog_event="ccu.relisted",
                                ship=ship,
                                sku_id=sku_id,
                                edition_label=sku_data.edition_label,
                                is_warbond=sku_data.is_warbond,
                                old_price_cents=old_price,
                                new_price_cents=sku_data.price_cents,
                                original_price_cents=orig_cents,
                                expires_at=sku_data.expires_at,
                                store_url=old_sku.store_url or ship.store_url or UPGRADE_PAGE_URL,
                                candidates=candidate_pool,
                                now=now,
                            )
                        )
                    elif sku_data.price_cents != old_price:
                        pending_ccu_price_changes.append(
                            (
                                ship,
                                sku_id,
                                sku_data,
                                old_price,
                                orig_cents,
                                old_sku.store_url,
                            )
                        )

                # 对 CCU 价格变动执行批量税率纠偏与单条 $5 倍数校验，过滤异常价格刷新
                if pending_ccu_price_changes:
                    ccu_pairs = [
                        (old_p, s_data.price_cents)
                        for _, _, s_data, old_p, _, _ in pending_ccu_price_changes
                    ]
                    if is_abnormal_price_change_batch(ccu_pairs):
                        logger.warning(
                            "Suppressed abnormal CCU price-change batch (count=%d)",
                            len(pending_ccu_price_changes),
                        )
                    else:
                        for (
                            ship_item,
                            s_id,
                            s_data,
                            old_p,
                            o_cents,
                            sku_url,
                        ) in pending_ccu_price_changes:
                            if is_abnormal_price_change(old_p, s_data.price_cents):
                                logger.debug(
                                    "Skipped abnormal CCU price change sku=%s (%s -> %s)",
                                    s_id,
                                    old_p,
                                    s_data.price_cents,
                                )
                                continue
                            price_changed_count += 1
                            if s_data.price_cents < old_p:
                                events_to_publish.append(
                                    self._record_change_and_build_event(
                                        session=session,
                                        bus_event=EventType.CCU_PRICE_CHANGED,
                                        webhook_event="ccu.price_changed",
                                        changelog_event="ccu.price_changed",
                                        ship=ship_item,
                                        sku_id=s_id,
                                        edition_label=s_data.edition_label,
                                        is_warbond=s_data.is_warbond,
                                        old_price_cents=old_p,
                                        new_price_cents=s_data.price_cents,
                                        original_price_cents=o_cents,
                                        expires_at=s_data.expires_at,
                                        store_url=sku_url
                                        or ship_item.store_url
                                        or UPGRADE_PAGE_URL,
                                        candidates=candidate_pool,
                                        now=now,
                                    )
                                )
                            else:
                                session.add(
                                    Changelog(
                                        occurred_at=now,
                                        event_type="ccu.price_changed",
                                        sku_id=s_id,
                                        ship_name_en=ship_item.name_en,
                                        ship_name_zh=ship_item.name_zh,
                                        edition_label=s_data.edition_label,
                                        is_warbond=s_data.is_warbond,
                                        old_price_cents=old_p,
                                        new_price_cents=s_data.price_cents,
                                        original_price_cents=o_cents,
                                        details={"direction": "increased"},
                                    )
                                )

                # 3c. 处理连续缺失达到阈值的下架 SKU
                ship_by_id = {s.id: s for s in all_ships}
                for sku_id, old_sku in existing_skus.items():
                    if sku_id in seen_sku_map or not old_sku.listed:
                        continue
                    old_sku.misses += 1
                    if old_sku.misses >= MISSES_BEFORE_DELISTED:
                        old_sku.listed = False
                        delisted_count += 1
                        ship_ref = ship_by_id.get(old_sku.ship_id)
                        session.add(
                            Changelog(
                                occurred_at=now,
                                event_type="ccu.delisted",
                                sku_id=sku_id,
                                ship_name_en=ship_ref.name_en if ship_ref else old_sku.title,
                                ship_name_zh=ship_ref.name_zh if ship_ref else "",
                                edition_label=old_sku.edition_label,
                                is_warbond=old_sku.is_warbond,
                                old_price_cents=old_sku.price_cents,
                                new_price_cents=None,
                                original_price_cents=old_sku.original_price_cents,
                                details={"misses": old_sku.misses},
                            )
                        )

        # 4. 事务提交成功后，再统一向进程内 EventBus 发布外部通知事件
        for bus_event, webhook_event, event_tags, payload in events_to_publish:
            await self._event_bus.publish(
                bus_event,
                {
                    "webhook_event": webhook_event,
                    "event_tags": event_tags,
                    "payload": payload,
                },
            )

        if is_baseline:
            logger.info(
                "Initial CCU baseline captured silently (%d SKUs)",
                len(seen_sku_map),
            )
        else:
            logger.info(
                "CCU diff completed (seen=%d, listed=%d, relisted=%d, "
                "price_changed=%d, delisted=%d, events=%d)",
                len(seen_sku_map),
                listed_count,
                relisted_count,
                price_changed_count,
                delisted_count,
                len(events_to_publish),
            )

        return {
            "status": "ok",
            "baseline": is_baseline,
            "seen_skus": len(seen_sku_map),
            "listed": listed_count,
            "relisted": relisted_count,
            "price_changed": price_changed_count,
            "delisted": delisted_count,
            "published_events": len(events_to_publish),
        }

    def _record_change_and_build_event(
        self,
        session: AsyncSession,
        bus_event: EventType,
        webhook_event: str,
        changelog_event: str,
        ship: Ship,
        sku_id: int,
        edition_label: str,
        is_warbond: bool,
        old_price_cents: int | None,
        new_price_cents: int,
        original_price_cents: int,
        expires_at: datetime | None,
        store_url: str,
        candidates: Sequence[ShipCandidate],
        now: datetime,
    ) -> tuple[EventType, str, list[str], dict[str, Any]]:
        """
        在当前事务中写入 Changelog 并构建符合设计规范的标准化领域事件负载。

        @param session: 当前活跃异步数据库事务会话
        @param bus_event: 内部事件总线枚举类型
        @param webhook_event: 对外 Webhook 事件名
        @param changelog_event: Changelog 表记录的事件类型
        @param ship: 目标舰船 ORM 实体
        @param sku_id: CCU SKU ID
        @param edition_label: 版本标签
        @param is_warbond: 是否战争债券版
        @param old_price_cents: 历史价格（美分）或 None
        @param new_price_cents: 当前购买价（美分）
        @param original_price_cents: 舰船原价（美分）
        @param expires_at: 到期 UTC 时间或 None
        @param store_url: 商店地址
        @param candidates: 候选起始船池
        @param now: 当前 UTC 时间
        @return: (EventType, webhook_event_name, event_tags, 标准 Webhook Payload) 四元组
        """
        nearest = find_nearest_from_ships(new_price_cents, candidates, limit=5)
        savings_cents = max(0, original_price_cents - new_price_cents)
        is_special = _is_special_sale_edition(edition_label)
        sale_type = (
            "warbond_upgrade"
            if is_warbond
            else ("special_sale" if is_special else "standard_upgrade")
        )
        event_tags: list[str] = [webhook_event]
        if is_warbond:
            event_tags.append("ccu.warbond_upgrade")
        if is_special:
            event_tags.append("store.special_sale")

        session.add(
            Changelog(
                occurred_at=now,
                event_type=changelog_event,
                sku_id=sku_id,
                ship_name_en=ship.name_en,
                ship_name_zh=ship.name_zh,
                edition_label=edition_label,
                is_warbond=is_warbond,
                old_price_cents=old_price_cents,
                new_price_cents=new_price_cents,
                original_price_cents=original_price_cents,
                details={"sku_id": sku_id, "nearest_count": len(nearest)},
            )
        )

        payload = {
            "event": webhook_event,
            "timestamp": _format_iso_utc(now),
            "data": {
                "ship_id": str(ship.rsi_upgrade_id or ship.id),
                "sku_id": sku_id,
                "ship_name_en": ship.name_en,
                "ship_name_zh": ship.name_zh,
                "edition_label": edition_label,
                "sale_type": sale_type,
                "is_warbond": is_warbond,
                "is_special_sale": is_special,
                "original_price": round(original_price_cents / 100, 2),
                "discounted_price": round(new_price_cents / 100, 2),
                "previous_price": (
                    round(old_price_cents / 100, 2) if old_price_cents is not None else None
                ),
                "savings": round(savings_cents / 100, 2),
                "currency": "USD",
                "expires_at": _format_iso_utc(expires_at),
                "store_url": store_url or UPGRADE_PAGE_URL,
                "image_url": to_absolute_url(ship.image_url),
                "nearest_from_ships": nearest,
            },
        }
        return bus_event, webhook_event, event_tags, payload

    async def list_active_ccus(
        self,
        query: str | None = None,
        warbond_only: bool = False,
        min_price: float | None = None,
        max_price: float | None = None,
        min_savings: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        查询当前所有在售 CCU 并实时附加最近可升级起始船 Top 5 推荐清单。

        @param query: 双语搜索关键字
        @param warbond_only: 是否仅返回 WBCCU
        @param min_price: 最低现价（美元）
        @param max_price: 最高现价（美元）
        @param min_savings: 最低立省金额（美元）
        @return: 包含最近可升级起始船的 CCU 字典列表
        """
        min_price_cents = int(round(min_price * 100)) if min_price is not None else None
        max_price_cents = int(round(max_price * 100)) if max_price is not None else None
        min_savings_cents = int(round(min_savings * 100)) if min_savings is not None else None

        async with self._session_factory() as session:
            rows = await self._ccu_repo.list_listed_with_ships(
                session=session,
                query=query,
                warbond_only=warbond_only,
                min_price_cents=min_price_cents,
                max_price_cents=max_price_cents,
                min_savings_cents=min_savings_cents,
            )
            all_ships = await self._ship_repo.list_all(session)

        candidates = [
            ShipCandidate(
                name_en=s.name_en,
                name_zh=s.name_zh,
                value_cents=s.value_cents,
                always_available=s.always_available,
            )
            for s in all_ships
            if s.value_cents > 0
        ]

        results: list[dict[str, Any]] = []
        for sku, ship in rows:
            savings_cents = max(0, sku.original_price_cents - sku.price_cents)
            results.append(
                {
                    "sku_id": sku.sku_id,
                    "ship_id": ship.id,
                    "rsi_upgrade_id": ship.rsi_upgrade_id,
                    "ship_name_en": ship.name_en,
                    "ship_name_zh": ship.name_zh,
                    "manufacturer": ship.manufacturer,
                    "title": sku.title,
                    "edition_label": sku.edition_label,
                    "is_warbond": sku.is_warbond,
                    "original_price": round(sku.original_price_cents / 100, 2),
                    "discounted_price": round(sku.price_cents / 100, 2),
                    "savings": round(savings_cents / 100, 2),
                    "currency": "USD",
                    "expires_at": ensure_utc(sku.expires_at),
                    "store_url": sku.store_url or ship.store_url or UPGRADE_PAGE_URL,
                    "image_url": ship.image_url,
                    "listed_since_at": ensure_utc(sku.listed_since_at),
                    "last_seen_at": ensure_utc(sku.last_seen_at),
                    "nearest_from_ships": find_nearest_from_ships(
                        sku.price_cents, candidates, limit=5
                    ),
                }
            )
        return results

    async def list_ships(
        self,
        query: str | None = None,
        always_available_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        查询全量舰船及中英文映射清单。

        @param query: 双语检索关键字
        @param always_available_only: 是否仅返回常驻可买舰船
        @return: 舰船字典列表
        """
        async with self._session_factory() as session:
            ships = await self._ship_repo.list_filtered(
                session=session,
                query=query,
                always_available_only=always_available_only,
            )

        return [
            {
                "id": s.id,
                "key": s.key,
                "rsi_upgrade_id": s.rsi_upgrade_id,
                "name_en": s.name_en,
                "name_zh": s.name_zh,
                "auto_translated": s.auto_translated,
                "manufacturer": s.manufacturer,
                "focus": s.focus,
                "size": s.size,
                "production_status": s.production_status,
                "value": round(s.value_cents / 100, 2),
                "always_available": s.always_available,
                "in_store": s.in_store,
                "store_url": s.store_url,
                "image_url": s.image_url,
                "updated_at": ensure_utc(s.updated_at),
            }
            for s in ships
        ]

    async def list_changelog(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        query: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        分页查询历史上下架与调价记录。

        @param limit: 单页条数
        @param offset: 分页偏移量
        @param event_type: 可选事件类型过滤
        @param query: 可选双语船名或版本关键词
        @return: (变动记录字典列表, 总匹配条数)
        """
        async with self._session_factory() as session:
            items, total = await self._changelog_repo.list_recent(
                session=session,
                limit=limit,
                offset=offset,
                event_type=event_type,
                query=query,
            )

        serialized = [
            {
                "id": item.id,
                "occurred_at": ensure_utc(item.occurred_at),
                "event_type": item.event_type,
                "sku_id": item.sku_id,
                "ship_name_en": item.ship_name_en,
                "ship_name_zh": item.ship_name_zh,
                "edition_label": item.edition_label,
                "is_warbond": item.is_warbond,
                "old_price": (
                    round(item.old_price_cents / 100, 2)
                    if item.old_price_cents is not None
                    else None
                ),
                "new_price": (
                    round(item.new_price_cents / 100, 2)
                    if item.new_price_cents is not None
                    else None
                ),
                "original_price": (
                    round(item.original_price_cents / 100, 2)
                    if item.original_price_cents is not None
                    else None
                ),
                "details": dict(item.details or {}),
            }
            for item in items
        ]
        return serialized, total

    async def get_status_summary(self) -> dict[str, Any]:
        """
        获取系统看板状态汇总统计。

        @return: 包含在售 CCU 总数、WBCCU 数、船只总数、已汉化数与最后刷新时间的字典
        """
        async with self._session_factory() as session:
            total_ccus, warbond_ccus = await self._ccu_repo.count_listed(session)
            total_ships, translated_ships = await self._ship_repo.count_all(session)
            last_seen_at = await self._ccu_repo.get_last_seen_at(session)

        return {
            "total_listed_ccus": total_ccus,
            "warbond_ccus": warbond_ccus,
            "total_ships": total_ships,
            "translated_ships": translated_ships,
            "last_scrape_at": ensure_utc(last_seen_at),
        }

    def _load_cached_cargo_grids(self) -> tuple[str, dict[str, dict[str, Any]]]:
        """
        读取本地缓存的 sc-cargo.space 仓库 SHA 与网格布局映射表。

        @return: (tree_sha, grids_by_key) 二元组
        """
        cache_path = self._cargo_cache_path()
        if cache_path is None or not cache_path.is_file():
            return "", {}
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            sha = str(raw.get("sha") or "")
            grids = raw.get("grids")
            return sha, grids if isinstance(grids, dict) else {}
        except (OSError, ValueError):
            return "", {}

    def _save_cached_cargo_grids(self, sha: str, grids: dict[str, dict[str, Any]]) -> None:
        """
        原子写入本地货舱网格 JSON 缓存文件。

        @param sha: 最新 GitHub tree SHA
        @param grids: 解析后的全量网格字典
        """
        cache_path = self._cargo_cache_path()
        if cache_path is None:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps({"sha": sha, "grids": grids}, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_path.replace(cache_path)

    async def sync_catalog_and_enrichment(
        self,
        matrix_items: list[ScrapedShipMatrixItem] | None = None,
        standalone_items: list[ScrapedStandaloneShip] | None = None,
    ) -> dict[str, Any]:
        """
        执行全量目录更新与第三方外部数据富化（Loaners / Wiki / UEX / sc-cargo.space）。

        全部外部 HTTP 拉取在事务外完成，避免长耗时的网络 IO 持锁阻塞高频 CCU 巡检或写入请求。

        @param matrix_items: 可选的预抓取 Ship Matrix 条目（测试注入用）
        @param standalone_items: 可选的预抓取单船商店条目（测试注入用）
        @return: 包含富化统计数字的汇总字典
        """
        await self.sync_catalog_and_standalone(
            matrix_items=matrix_items,
            standalone_items=standalone_items,
        )
        if self._enrichment is None:
            return {"status": "skipped", "reason": "no_enrichment_client"}

        async with self._session_factory() as session:
            ships = await self._ship_repo.list_all(session)
            store_skus = await self._store_repo.list_all_store_skus(session)

        if not ships:
            return {"status": "empty", "enriched_ships": 0}

        ship_names = [s.name_en for s in ships]
        matrix_id_by_key = {s.key: s.rsi_matrix_id for s in ships if s.rsi_matrix_id}
        known_sha, cached_grids = self._load_cached_cargo_grids()

        # 并发采集非 RSI 同源扩展数据源（内部分别做异常容错）
        loaner_rules, wiki_bundle, ingame_map, cargo_res = await asyncio.gather(
            self._enrichment.fetch_loaners(),
            self._enrichment.fetch_wiki_release_and_gold(ship_names),
            self._enrichment.fetch_ingame_and_wiki_vehicles(matrix_id_by_key),
            self._enrichment.fetch_cargo_grids(known_sha if cached_grids else ""),
        )

        new_sha, downloaded_grids = cargo_res
        if downloaded_grids is not None:
            cached_grids = downloaded_grids
            self._save_cached_cargo_grids(new_sha, cached_grids)

        flight_ready_names = [
            s.name_en for s in ships if s.production_status in ("", "flight-ready")
        ]
        released_map = fill_variant_values(wiki_bundle.released, flight_ready_names)
        availability_map = fill_variant_values(wiki_bundle.availability, ship_names)

        # 统计各船关联的模块名集合以便匹配模块货舱布局
        ships_by_id = {s.id: s for s in ships}
        modules_by_ship_id: dict[int, set[str]] = {}
        for sku in store_skus:
            if not sku.listed or sku.ship_id is None:
                continue
            if sku.category == "module":
                ship_ref = ships_by_id.get(sku.ship_id)
                if ship_ref:
                    head = re.split(r"\bmodule\b", sku.raw_name, maxsplit=1, flags=re.IGNORECASE)[0]
                    words = re.findall(r"[A-Za-z0-9]+", head)
                    base_words = ship_ref.key.split()
                    mod_label = " ".join(words[len(base_words) :]).strip()
                    if mod_label:
                        modules_by_ship_id.setdefault(sku.ship_id, set()).add(mod_label)
            elif sku.extras:
                for mod_label in extract_module_labels(sku.extras):
                    modules_by_ship_id.setdefault(sku.ship_id, set()).add(mod_label)

        now = utc_now()
        enriched_count = 0
        async with self._session_factory() as session:
            async with session.begin():
                db_ships = await self._ship_repo.list_all(session)
                for ship in db_ships:
                    if wiki_bundle.released or wiki_bundle.availability:
                        rel_val = released_map.get(ship.name_en, "")
                        if ship.production_status in ("", "flight-ready"):
                            ship.released_patch = rel_val
                        avail_val = availability_map.get(ship.name_en, "")
                        if avail_val:
                            ship.pledge_availability = avail_val

                    if wiki_bundle.gold_rows:
                        g_st, g_patch, g_fam = resolve_gold_for_ship(
                            ship.name_en, wiki_bundle.gold_rows
                        )
                        ship.gold_status = g_st
                        ship.gold_patch = g_patch
                        ship.gold_family = g_fam

                    if loaner_rules:
                        if ship.production_status and ship.production_status != "flight-ready":
                            ship.loaners = match_loaner_for_key(ship.key, loaner_rules)
                        else:
                            ship.loaners = ""

                    ig = ingame_map.get(ship.rsi_matrix_id) if ship.rsi_matrix_id else None
                    if ig is not None:
                        if ig.game_version:
                            ship.game_version = ig.game_version
                        if ig.class_name:
                            ship.class_name = ig.class_name
                            resolved = self._i18n_service.resolve_local(
                                ship.name_en, class_name=ship.class_name
                            )
                            if resolved.name_zh and (
                                resolved.source in ("override", "cache") or not ship.name_zh
                            ):
                                ship.name_zh = resolved.name_zh
                                ship.auto_translated = resolved.auto_translated
                            maker_zh = self._i18n_service.resolve_manufacturer_zh(
                                ship.manufacturer, ship.manufacturer_code
                            )
                            ship.search_index = build_search_index(
                                ship.name_en, ship.name_zh, ship.manufacturer, maker_zh
                            )
                        ship.claim_time_min = ig.claim_time_min
                        ship.expedite_time_min = ig.expedite_time_min
                        ship.expedite_cost_auec = ig.expedite_cost_auec
                        ship.ingame_buy = ig.buy_locations
                        ship.ingame_rent = ig.rent_locations
                        ship.weapons_summary = ig.weapons_summary
                        ship.price_history = ig.price_history

                    if cached_grids:
                        base_grid = find_cargo_grid(cached_grids, ship.name_en)
                        ship.cargo_grid = base_grid
                        if base_grid and base_grid.get("scu"):
                            ship.cargo_scu = float(base_grid["scu"])
                        mod_grids: dict[str, Any] = {}
                        for label in sorted(modules_by_ship_id.get(ship.id, set())):
                            alias = MODULE_GRID_ALIASES.get(label.lower(), label.lower())
                            mg = cached_grids.get(normalize_ship_key(f"{ship.name_en} {alias}"))
                            if mg and mg is not base_grid:
                                mod_grids[label] = mg
                        ship.module_grids = mod_grids

                    ship.updated_at = now
                    enriched_count += 1

        logger.info("Catalog enrichment completed (enriched_ships=%d)", enriched_count)
        return {
            "status": "ok",
            "enriched_ships": enriched_count,
            "ingame_matched": len(ingame_map),
            "cargo_grids": len(cached_grids),
        }

    async def get_upgrade_costs(self, from_upgrade_ship_id: int) -> dict[str, Any] | None:
        """
        查询指定起始船当前可升级到的所有在售 CCU SKU 及精确差价（带 10 分钟内存缓存）。

        @param from_upgrade_ship_id: 起始船在 RSI 升级工具中的 ship id
        @return: {"ref", "at", "costs": {sku_str: cost_cents}}；若起始船不存在返回 None
        @raise RsiScrapeError: 实时向 RSI 查询失败且无可用缓存时抛出
        """
        async with self._session_factory() as session:
            from_ship = await self._ship_repo.get_by_upgrade_id(session, from_upgrade_ship_id)
        if from_ship is None:
            return None

        lock = self._costs_locks.setdefault(from_upgrade_ship_id, asyncio.Lock())
        async with lock:
            now_ts = time.time()
            cached = self._costs_cache.get(from_upgrade_ship_id)
            if cached and (now_ts - cached[0]) <= COSTS_CACHE_TTL_SECONDS:
                return {
                    "ref": from_upgrade_ship_id,
                    "at": int(cached[0]),
                    "costs": cached[1],
                }

            try:
                raw_costs = await self._scraper.fetch_upgrade_costs_from(from_upgrade_ship_id)
            except RsiScrapeError:
                if cached:
                    return {
                        "ref": from_upgrade_ship_id,
                        "at": int(cached[0]),
                        "costs": cached[1],
                    }
                raise

            # 用数据库记录的税前 USD 价差修正代理地区可能附带的 VAT 税率差
            async with self._session_factory() as session:
                listed_skus = {
                    sku.sku_id: sku
                    for sku, _ in await self._ccu_repo.list_listed_with_ships(session)
                }

            normalized: dict[str, int] = {}
            from_val = from_ship.value_cents
            for sku_id, rsi_diff in raw_costs.items():
                sku_row = listed_skus.get(sku_id)
                if sku_row and from_val > 0 and sku_row.price_cents > from_val:
                    normalized[str(sku_id)] = sku_row.price_cents - from_val
                else:
                    normalized[str(sku_id)] = rsi_diff

            self._costs_cache[from_upgrade_ship_id] = (now_ts, normalized)
            return {
                "ref": from_upgrade_ship_id,
                "at": int(now_ts),
                "costs": normalized,
            }

    async def sync_upgrade_paths_map(
        self,
        should_stop: Callable[[], bool] | None = None,
        pause_seconds: float = 0.35,
    ) -> int:
        """
        周期性遍历全部起始底船建立升级路线规划可达图并写入 upgrade_paths 表。

        @param should_stop: 可选提前终止判定回调（用于停机时安全中止）
        @param pause_seconds: 每艘船查询后的温和节流间隔秒数
        @return: 成功入库的起始船节点数量
        """
        async with self._session_factory() as session:
            all_ships = await self._ship_repo.list_all(session)
        candidates = [
            s
            for s in all_ships
            if s.rsi_upgrade_id is not None and s.value_cents > 0
        ]
        if not candidates:
            return 0

        found: dict[int, dict[int, int]] = {}
        try:
            async with self._scraper.open_upgrade_session(timeout_seconds=15.0) as (
                client,
                headers,
            ):
                for idx, ship in enumerate(candidates):
                    if should_stop and should_stop():
                        logger.info("Upgrade map sync interrupted gracefully during shutdown")
                        return 0
                    ref = ship.rsi_upgrade_id
                    if ref is None:
                        continue
                    try:
                        if idx > 0 and idx % 20 == 0:
                            headers = await self._scraper.establish_upgrade_headers(client)
                        costs = await self._scraper.query_upgrade_costs_with_client(
                            client, headers, ref
                        )
                        found[ref] = costs
                    except RsiScrapeError as exc:
                        logger.warning("Upgrade map query failed for ship_ref=%s: %s", ref, exc)
                    if pause_seconds > 0:
                        await asyncio.sleep(pause_seconds)
        except RsiScrapeError as exc:
            logger.warning("Upgrade map handshake aborted: %s", exc)
            return 0

        if len(found) < len(candidates) * PLAN_MIN_COVERAGE_SHARE:
            logger.warning(
                "Upgrade map coverage too low (%d/%d); keeping previous paths",
                len(found),
                len(candidates),
            )
            return 0

        now = utc_now()
        async with self._session_factory() as session:
            async with session.begin():
                edge_count = await self._store_repo.replace_upgrade_paths_for_ships(
                    session, found, now
                )
        logger.info(
            "Upgrade planner map updated (%d ships, %d edges)",
            len(found),
            edge_count,
        )
        return len(found)

    async def get_upgrade_plan_map(self) -> dict[str, Any]:
        """
        构建供升级路线规划器（/plan）使用的全量节点与可达有向边映射数据。

        当后台尚未完成首轮全量路径映射时，自动基于价格差生成初始预估边并标记 estimated=True。

        @return: 包含 at、estimated、ships、skus、paths 的字典
        """
        async with self._session_factory() as session:
            all_ships = await self._ship_repo.list_all(session)
            listed_skus = await self._ccu_repo.list_listed_with_ships(session)
            stored_paths = await self._store_repo.list_all_upgrade_paths(session)
            paths_updated_at = await self._store_repo.get_latest_paths_updated_at(session)

        upgrade_ships = [
            s
            for s in all_ships
            if s.rsi_upgrade_id is not None and s.value_cents > 0
        ]
        ship_by_ref = {
            s.rsi_upgrade_id: s for s in upgrade_ships if s.rsi_upgrade_id is not None
        }
        skus_by_id = {sku.sku_id: sku for sku, _ in listed_skus}

        # 每艘船格式: [ref_id, name_en, name_zh, value_cents, in_store, always_available]
        ships_payload = [
            [
                s.rsi_upgrade_id,
                s.name_en,
                s.name_zh,
                s.value_cents,
                s.in_store,
                s.always_available,
            ]
            for s in upgrade_ships
        ]
        # 每个 SKU 格式: [target_ref_id, label, price_cents, is_warbond]
        skus_payload = {
            str(sku.sku_id): [
                sku.rsi_upgrade_ship_id,
                sku.edition_label,
                sku.price_cents,
                sku.is_warbond,
            ]
            for sku, _ in listed_skus
        }

        paths_payload: dict[str, list[list[int]]] = {}
        estimated = False
        if stored_paths:
            for row in stored_paths:
                sku_row = skus_by_id.get(row.sku_id)
                from_ship = ship_by_ref.get(row.from_upgrade_ship_id)
                if not sku_row or not from_ship:
                    continue
                if sku_row.price_cents > from_ship.value_cents:
                    cost = sku_row.price_cents - from_ship.value_cents
                    paths_payload.setdefault(str(row.from_upgrade_ship_id), []).append(
                        [row.sku_id, cost]
                    )
        else:
            estimated = True
            for from_ship in upgrade_ships:
                ref = from_ship.rsi_upgrade_id
                if ref is None:
                    continue
                edges: list[list[int]] = []
                for sku, _ in listed_skus:
                    if sku.rsi_upgrade_ship_id == ref:
                        continue
                    if sku.price_cents > from_ship.value_cents:
                        edges.append([sku.sku_id, sku.price_cents - from_ship.value_cents])
                if edges:
                    edges.sort(key=lambda pair: pair[1])
                    paths_payload[str(ref)] = edges

        for edge_list in paths_payload.values():
            edge_list.sort(key=lambda pair: pair[1])

        return {
            "at": _format_iso_utc(paths_updated_at),
            "estimated": estimated,
            "ships": ships_payload,
            "skus": skus_payload,
            "paths": paths_payload,
        }

    async def get_full_store_payload(self) -> dict[str, Any]:
        """
        聚合全量单船商店版本、独立模块、在售 CCU、游戏内买租、保险、武器与货舱布局数据。

        @return: 兼容原版 PledgeWatch 商店首页渲染所需的完整响应字典
        """
        async with self._session_factory() as session:
            all_ships = await self._ship_repo.list_all(session)
            all_store_skus = await self._store_repo.list_all_store_skus(session)
            listed_ccus = await self._ccu_repo.list_listed_with_ships(session)
            last_ccu_at = await self._ccu_repo.get_last_seen_at(session)
            changes, _ = await self._changelog_repo.list_recent(session, limit=150, offset=0)

        store_by_ship: dict[int, list[StoreSku]] = {}
        modules_by_ship: dict[int, list[StoreSku]] = {}
        first_seen_at: datetime | None = None
        last_store_at: datetime | None = None

        for sku in all_store_skus:
            if first_seen_at is None or sku.first_seen_at < first_seen_at:
                first_seen_at = sku.first_seen_at
            if last_store_at is None or sku.last_seen_at > last_store_at:
                last_store_at = sku.last_seen_at
            if sku.ship_id is None:
                continue
            if sku.category == "module":
                if sku.listed:
                    modules_by_ship.setdefault(sku.ship_id, []).append(sku)
            else:
                store_by_ship.setdefault(sku.ship_id, []).append(sku)

        ccus_by_ship: dict[int, list[CcuSku]] = {}
        for ccu_sku, ship_row in listed_ccus:
            ccus_by_ship.setdefault(ship_row.id, []).append(ccu_sku)

        game_version = ""
        ship_items: list[dict[str, Any]] = []
        ship_by_id = {s.id: s for s in all_ships}
        candidate_pool = [
            ShipCandidate(
                name_en=s.name_en,
                name_zh=s.name_zh,
                value_cents=s.value_cents,
                always_available=s.always_available,
            )
            for s in all_ships
            if s.value_cents > 0
        ]

        for ship in all_ships:
            if ship.game_version and ship.game_version > game_version:
                game_version = ship.game_version

            # 组装单船在售及历史版本卡片
            editions: list[dict[str, Any]] = []
            for s_sku in store_by_ship.get(ship.id, []):
                extras = list(s_sku.extras or [])
                paints_count = sum(1 for ex in extras if "paint" in ex.lower())
                includes = [ex for ex in extras if "hangar" not in ex.lower()]
                editions.append(
                    {
                        "label": s_sku.edition_label,
                        "price": s_sku.price_cents,
                        "list_price": s_sku.list_price_cents,
                        "in_store": s_sku.listed,
                        "available": s_sku.available,
                        "limited": s_sku.limited,
                        "level": s_sku.stock_level,
                        "url": s_sku.store_url,
                        "since": ensure_utc(s_sku.listed_since_at),
                        "last_seen": ensure_utc(s_sku.last_seen_at),
                        "insurance": s_sku.insurance,
                        "paints": paints_count,
                        "includes": includes,
                        "modules": extract_module_labels(extras),
                    }
                )
            editions.sort(
                key=lambda ed: (
                    not ed["in_store"],
                    EDITION_SORT_ORDER.get(ed["label"], 9),
                    ed["price"] or 0,
                )
            )

            # 组装单卖模块数据
            modules_list: list[dict[str, Any]] = []
            for m_sku in sorted(modules_by_ship.get(ship.id, []), key=lambda x: x.price_cents):
                head = re.split(r"\bmodule\b", m_sku.raw_name, maxsplit=1, flags=re.IGNORECASE)[0]
                words = re.findall(r"[A-Za-z0-9]+", head)
                mod_name = " ".join(words[len(ship.key.split()) :]).strip() or m_sku.raw_name
                modules_list.append(
                    {
                        "name": mod_name,
                        "price": m_sku.price_cents,
                        "list_price": m_sku.list_price_cents,
                        "url": m_sku.store_url,
                        "available": m_sku.available,
                        "insurance": m_sku.insurance,
                        "label": m_sku.edition_label,
                    }
                )

            # 组装升级 (CCU) 数据及最近可升级起始底船
            ship_donor_pool = [c for c in candidate_pool if c.name_en != ship.name_en]
            upgrades_list = [
                {
                    "sku": u.sku_id,
                    "label": u.edition_label,
                    "price": u.price_cents,
                    "is_warbond": u.is_warbond,
                    "expires_at": ensure_utc(u.expires_at),
                    "nearest_from_ships": (
                        find_nearest_from_ships(u.price_cents, ship_donor_pool, limit=12)
                        if u.price_cents > 0
                        else []
                    ),
                }
                for u in sorted(ccus_by_ship.get(ship.id, []), key=lambda x: x.price_cents)
            ]

            ccu_meta = (
                {"ref": ship.rsi_upgrade_id, "value": ship.value_cents}
                if ship.rsi_upgrade_id is not None and ship.value_cents > 0
                else None
            )

            crew_str = ""
            if ship.min_crew or ship.max_crew:
                lo, hi = int(ship.min_crew or 0), int(ship.max_crew or 0)
                crew_str = str(lo or hi) if (lo == hi or not hi) else f"{lo}–{hi}"

            insurance_meta = (
                {
                    "claim": round(ship.claim_time_min, 1),
                    "expedite": round(ship.expedite_time_min or 0.0, 1),
                    "expedite_cost": int(ship.expedite_cost_auec or 0),
                }
                if ship.claim_time_min
                else None
            )

            gold_meta = (
                {
                    "status": ship.gold_status,
                    "patch": ship.gold_patch,
                    "family": ship.gold_family,
                }
                if ship.gold_status
                else None
            )

            ship_items.append(
                {
                    "id": ship.id,
                    "key": ship.key,
                    "name_en": ship.name_en,
                    "name_zh": ship.name_zh,
                    "maker": ship.manufacturer,
                    "maker_code": ship.manufacturer_code,
                    "role": ship.focus,
                    "type": ship.ship_type,
                    "size": ship.size.title() if ship.size else "",
                    "crew": crew_str,
                    "cargo": ship.cargo_scu,
                    "length": ship.length_m,
                    "status": SHIP_STATUS_LABELS.get(
                        ship.production_status, ship.production_status
                    ),
                    "loaners": ship.loaners,
                    "released": (
                        ship.released_patch
                        if ship.production_status in ("", "flight-ready")
                        else ""
                    ),
                    "sold": ship.pledge_availability,
                    "gold": gold_meta,
                    "img": ship.image_url,
                    "rsi": ship.store_url,
                    "always_available": ship.always_available,
                    "in_store": ship.in_store or any(e["in_store"] for e in editions),
                    "editions": editions,
                    "modules": modules_list,
                    "ccu": ccu_meta,
                    "upgrades": upgrades_list,
                    "insurance": insurance_meta,
                    "buy": [[row[0], row[1]] for row in (ship.ingame_buy or [])],
                    "rent": [[row[0], row[1]] for row in (ship.ingame_rent or [])],
                    "weapons": ship.weapons_summary,
                    "class_name": ship.class_name,
                    "history": [h[:4] for h in (ship.price_history or [])],
                    "grid": ship.cargo_grid,
                    "module_grids": dict(ship.module_grids or {}),
                }
            )

        checked_at = max(
            (dt for dt in (last_ccu_at, last_store_at) if dt is not None),
            default=None,
        )
        serialized_changes = [
            {
                "id": ch.id,
                "occurred_at": ensure_utc(ch.occurred_at),
                "event_type": ch.event_type,
                "sku_id": ch.sku_id,
                "ship_name_en": ch.ship_name_en,
                "ship_name_zh": ch.ship_name_zh,
                "edition_label": ch.edition_label,
                "is_warbond": ch.is_warbond,
                "old_price": (
                    round(ch.old_price_cents / 100, 2)
                    if ch.old_price_cents is not None
                    else None
                ),
                "new_price": (
                    round(ch.new_price_cents / 100, 2)
                    if ch.new_price_cents is not None
                    else None
                ),
                "original_price": (
                    round(ch.original_price_cents / 100, 2)
                    if ch.original_price_cents is not None
                    else None
                ),
                "details": dict(ch.details or {}),
            }
            for ch in changes
        ]

        _ = ship_by_id
        return {
            "ships": ship_items,
            "changes": serialized_changes,
            "game_version": game_version,
            "tracking_since": ensure_utc(first_seen_at),
            "checked_at": ensure_utc(checked_at),
        }
