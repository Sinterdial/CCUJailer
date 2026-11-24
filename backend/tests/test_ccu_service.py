"""
CCU 差价计算算法与状态 Diff 单元测试模块。

验证最近起始底船计算、首次冷启动静默基线、新上架与降价事件触发、连续缺失下架、
异常数量保护与到期时间解析逻辑。
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from cj.core.events import EventBus, EventType
from cj.repositories.store_repo import StoreRepository
from cj.scraper.rsi_client import (
    RsiScraperClient,
    ScrapedStandaloneShip,
    ScrapedUpgradeShip,
    ScrapedUpgradeSku,
    parse_rsi_datetime,
)
from cj.services.ccu_service import (
    CcuService,
    ShipCandidate,
    find_nearest_from_ships,
    is_abnormal_price_change,
    is_abnormal_price_change_batch,
    is_standard_usd_price,
)
from cj.services.i18n_service import I18nService


def test_find_nearest_from_ships_ranks_and_includes_always_available() -> None:
    """
    验证最近可升级底船算法严格过滤低价船、正确计算补差价并在结果中保障常驻底船展示。
    """
    candidates = [
        ShipCandidate("Prospector", "勘探者", 15500, always_available=True),
        ShipCandidate("Cutlass Red", "黑弯刀 红", 13500, always_available=True),
        ShipCandidate("Razor EX", "剃刀 EX", 15500, always_available=False),
        ShipCandidate("Sabre", "军刀", 17000, always_available=False),
        ShipCandidate("F7C Hornet", "F7C 大黄蜂", 11000, always_available=True),
        ShipCandidate("Gladius", "短剑", 9000, always_available=True),
        ShipCandidate("Zeus Mk II ES", "宙斯 Mk II ES", 17500, always_available=True),
        ShipCandidate("Polaris", "北极星", 97500, always_available=False),
    ]

    result = find_nearest_from_ships(16000, candidates, limit=5)

    assert len(result) == 5
    # 17500 与 97500 不应入选（须严格小于目标价 16000）
    names = [item["name_en"] for item in result]
    assert "Zeus Mk II ES" not in names
    assert "Polaris" not in names
    # 17000 > 16000 同样不应入选；前两位应为 15500（且常驻 Prospector 排在 Razor EX 前）
    assert result[0] == {
        "name_en": "Prospector",
        "name_zh": "勘探者",
        "value": 155.0,
        "upgrade_cost": 5.0,
        "always_available": True,
    }
    assert result[1]["name_en"] == "Razor EX"
    assert result[2]["name_en"] == "Cutlass Red"
    assert result[2]["upgrade_cost"] == 25.0


def test_find_nearest_from_ships_guarantees_always_available_slot() -> None:
    """
    验证当最接近的 top N 全为限定船时，末位自动替换为最近的常驻可买底船。
    """
    candidates = [
        ShipCandidate("Limited A", "限定 A", 19000, always_available=False),
        ShipCandidate("Limited B", "限定 B", 18500, always_available=False),
        ShipCandidate("Limited C", "限定 C", 18000, always_available=False),
        ShipCandidate("Always Base", "常驻底船", 12000, always_available=True),
    ]

    result = find_nearest_from_ships(20000, candidates, limit=3)

    assert len(result) == 3
    assert result[0]["name_en"] == "Limited A"
    assert result[1]["name_en"] == "Limited B"
    assert result[2]["name_en"] == "Always Base"
    assert result[2]["always_available"] is True
    assert result[2]["upgrade_cost"] == 80.0


def test_find_nearest_from_ships_rejects_non_positive_price() -> None:
    """
    验证传入小于等于 0 的目标价格时抛出 ValueError。
    """
    with pytest.raises(ValueError, match="目标价格必须大于 0"):
        find_nearest_from_ships(0, [])


def test_parse_rsi_datetime_formats() -> None:
    """
    验证到期时间解析支持 ISO-8601 字符串与空值兜底为 None。
    """
    parsed = parse_rsi_datetime("2026-11-24T16:00:00Z")
    assert parsed == datetime(2026, 11, 24, 16, 0, 0, tzinfo=UTC)
    assert parse_rsi_datetime(None) is None
    assert parse_rsi_datetime("") is None


async def test_ccu_diff_lifecycle_events_and_guards(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    ccu_repo,
    changelog_repo,
    mock_net,
    event_bus: EventBus,
    setting_service,
) -> None:
    """
    验证完整 CCU Diff 生命周期：
    1. 首次抓取静默建立基线，不发送事件；
    2. 新上架 WBCCU 与已有 SKU 降价时，事务提交后正确发布领域事件并包含最近底船；
    3. 返回数量暴跌低于 50% 时跳过防误删；
    4. 连续缺失两次后才标记为下架并写入 Changelog。
    """
    received_events: list[tuple[EventType, dict[str, Any]]] = []

    async def _capture(event_type: EventType):
        async def _handler(payload: dict[str, Any]) -> None:
            received_events.append((event_type, payload))

        return _handler

    event_bus.subscribe(EventType.CCU_LISTED, await _capture(EventType.CCU_LISTED))
    event_bus.subscribe(EventType.CCU_PRICE_CHANGED, await _capture(EventType.CCU_PRICE_CHANGED))

    i18n_service = I18nService(
        settings=isolated_settings,
        net=mock_net,
        event_bus=event_bus,
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        setting_service=setting_service,
    )
    ccu_service = CcuService(
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        ccu_repo=ccu_repo,
        changelog_repo=changelog_repo,
        scraper=RsiScraperClient(net=mock_net),
        i18n_service=i18n_service,
        event_bus=event_bus,
    )

    # 1. 首次基线快照：两艘常驻底船 + 两款标准 CCU（不发通知）
    baseline_ships = [
        ScrapedUpgradeShip(
            upgrade_ship_id=10,
            name="Prospector",
            key="prospector",
            msrp_cents=15500,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=1001,
                    title="Standard Edition",
                    edition_label="Standard",
                    is_warbond=False,
                    price_cents=15500,
                    expires_at=None,
                )
            ],
        ),
        ScrapedUpgradeShip(
            upgrade_ship_id=20,
            name="Carrack",
            key="carrack",
            msrp_cents=60000,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=2001,
                    title="Standard Edition",
                    edition_label="Standard",
                    is_warbond=False,
                    price_cents=60000,
                    expires_at=None,
                )
            ],
        ),
        ScrapedUpgradeShip(
            upgrade_ship_id=30,
            name="Polaris",
            key="polaris",
            msrp_cents=97500,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=3001,
                    title="Warbond Edition",
                    edition_label="Warbond",
                    is_warbond=True,
                    price_cents=85000,
                    expires_at=None,
                )
            ],
        ),
    ]
    res1 = await ccu_service.sync_and_diff_ccus(baseline_ships)
    await event_bus.wait_all()
    assert res1["baseline"] is True
    assert res1["published_events"] == 0
    assert received_events == []

    # 2. 第二轮：新增宙斯 Mk II ES WBCCU 且北极星 WBCCU 降价至 $800
    exp_time = datetime(2026, 11, 24, 16, 0, 0, tzinfo=UTC)
    round2_ships = [
        baseline_ships[0],
        baseline_ships[1],
        ScrapedUpgradeShip(
            upgrade_ship_id=30,
            name="Polaris",
            key="polaris",
            msrp_cents=97500,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=3001,
                    title="Warbond Edition",
                    edition_label="Warbond",
                    is_warbond=True,
                    price_cents=80000,
                    expires_at=exp_time,
                )
            ],
        ),
        ScrapedUpgradeShip(
            upgrade_ship_id=40,
            name="Zeus Mk II ES",
            key="zeus mk ii es",
            msrp_cents=17500,
            image_url="https://robertsspaceindustries.com/media/zeus-es.jpg",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=4001,
                    title="Warbond Edition",
                    edition_label="Warbond",
                    is_warbond=True,
                    price_cents=16000,
                    expires_at=exp_time,
                )
            ],
        ),
    ]
    res2 = await ccu_service.sync_and_diff_ccus(round2_ships)
    await event_bus.wait_all()
    assert res2["listed"] == 1
    assert res2["price_changed"] == 1
    assert res2["published_events"] == 2
    assert len(received_events) == 2

    zeus_ev = next(p for et, p in received_events if et == EventType.CCU_LISTED)
    zeus_data = zeus_ev["payload"]["data"]
    assert zeus_data["ship_name_en"] == "Zeus Mk II ES"
    assert zeus_data["ship_name_zh"] == "宙斯 Mk II ES"
    assert zeus_data["discounted_price"] == 160.0
    assert zeus_data["savings"] == 15.0
    assert zeus_data["image_url"] == "https://robertsspaceindustries.com/media/zeus-es.jpg"
    assert zeus_data["expires_at"] == "2026-11-24T16:00:00Z"
    assert zeus_data["nearest_from_ships"][0]["name_en"] == "Prospector"
    assert zeus_data["nearest_from_ships"][0]["upgrade_cost"] == 5.0

    # 3. 暴跌保护测试：仅返回 1 个 SKU（低于 4 * 0.5 = 2）应被跳过
    res3 = await ccu_service.sync_and_diff_ccus([baseline_ships[0]])
    assert res3["status"] == "skipped"

    # 4. 连续缺失下架测试：移除 4001 后第 1 次仅增加 misses，第 2 次确认 delisted
    round4_ships = round2_ships[:3]
    res4 = await ccu_service.sync_and_diff_ccus(round4_ships)
    assert res4["delisted"] == 0
    res5 = await ccu_service.sync_and_diff_ccus(round4_ships)
    assert res5["delisted"] == 1

    logs, _ = await ccu_service.list_changelog(event_type="ccu.delisted")
    assert len(logs) == 1
    assert logs[0]["sku_id"] == 4001


async def test_abnormal_price_changes_suppressed_for_standalone_and_ccu(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    ccu_repo,
    changelog_repo,
    mock_net,
    event_bus: EventBus,
    setting_service,
) -> None:
    """
    验证非 $5 整倍数单条调价与同批统一税率纠偏（>= 8 条）在单船及 CCU 同步中均被抑制，
    同时合法的 $5 整数倍调价仍可正常记录 Changelog 并触发事件。
    """
    # 1. 纯函数边界校验
    assert is_standard_usd_price(15000) is True
    assert is_standard_usd_price(15001) is False
    assert is_standard_usd_price(0) is False
    assert is_standard_usd_price(None) is False
    assert is_abnormal_price_change(15000, 14500) is False
    assert is_abnormal_price_change(16500, 16123) is True
    assert is_abnormal_price_change(11020, 10000) is True
    assert is_abnormal_price_change_batch([(11000, 10000)] * 7) is False
    assert is_abnormal_price_change_batch([(11000, 10000)] * 8) is True

    received_events: list[tuple[EventType, dict[str, Any]]] = []

    async def _capture(et: EventType):
        async def _handler(payload: dict[str, Any]) -> None:
            received_events.append((et, payload))

        return _handler

    for et in (
        EventType.STORE_LISTED,
        EventType.STORE_PRICE_CHANGED,
        EventType.CCU_LISTED,
        EventType.CCU_PRICE_CHANGED,
    ):
        event_bus.subscribe(et, await _capture(et))

    i18n_service = I18nService(
        settings=isolated_settings,
        net=mock_net,
        event_bus=event_bus,
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        setting_service=setting_service,
    )
    ccu_service = CcuService(
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        ccu_repo=ccu_repo,
        changelog_repo=changelog_repo,
        scraper=RsiScraperClient(net=mock_net),
        i18n_service=i18n_service,
        event_bus=event_bus,
        store_repo=StoreRepository(),
        settings=isolated_settings,
    )

    def _standalone(idx: int, price_cents: int, *, wb: bool = False) -> ScrapedStandaloneShip:
        return ScrapedStandaloneShip(
            sku_id=f"st-{idx}",
            matrix_id=f"m-{idx}",
            name=f"Test Ship {idx}",
            key=f"test ship {idx}",
            price_cents=price_cents,
            available=True,
            limited=False,
            is_warbond=wb,
            store_url=f"/pledge/test-{idx}",
            image_url=f"/media/test-{idx}.jpg",
            category="ship",
            edition_label="Warbond" if wb else "Standard",
        )

    # 2. 单船商店同步：第 1 轮建立基线 -> 第 2 轮 8 条 1.1x 回落整批抑制 -> 第 3 轮仅合法 $5 放行
    st_base = [_standalone(i, 11000 * i) for i in range(1, 9)] + [
        _standalone(9, 16500),
        _standalone(10, 16500, wb=True),
    ]
    await ccu_service.sync_standalone_store(st_base)
    await event_bus.wait_all()
    assert received_events == []

    st_batch = [_standalone(i, 10000 * i) for i in range(1, 9)] + st_base[8:]
    await ccu_service.sync_standalone_store(st_batch)
    await event_bus.wait_all()
    st_logs, _ = await ccu_service.list_changelog(event_type="store.price_changed")
    assert st_logs == []
    assert received_events == []

    st_single = st_batch[:8] + [
        _standalone(9, 16123),
        _standalone(10, 15000, wb=True),
    ]
    await ccu_service.sync_standalone_store(st_single)
    await event_bus.wait_all()
    st_logs, _ = await ccu_service.list_changelog(event_type="store.price_changed")
    assert len(st_logs) == 1
    assert st_logs[0]["old_price"] == 165.0
    assert st_logs[0]["new_price"] == 150.0
    assert len(received_events) == 1
    assert received_events[0][0] == EventType.STORE_PRICE_CHANGED

    # 3. CCU 商店同步：8 条 1.1x 回落整批抑制 -> 孤立非 $5 抑制 -> 合法 $5 降价放行
    def _upgrade(idx: int, price_cents: int, *, wb: bool = False) -> ScrapedUpgradeShip:
        return ScrapedUpgradeShip(
            upgrade_ship_id=100 + idx,
            name=f"CCU Ship {idx}",
            key=f"ccu ship {idx}",
            msrp_cents=price_cents,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=5000 + idx,
                    title="Warbond Edition" if wb else "Standard Edition",
                    edition_label="Warbond" if wb else "Standard",
                    is_warbond=wb,
                    price_cents=price_cents,
                    expires_at=None,
                )
            ],
        )

    ccu_base = [_upgrade(i, 11000 * i) for i in range(1, 9)] + [
        _upgrade(9, 16500),
        _upgrade(10, 16500, wb=True),
    ]
    res_base = await ccu_service.sync_and_diff_ccus(ccu_base)
    assert res_base["baseline"] is True

    ccu_batch = [_upgrade(i, 10000 * i) for i in range(1, 9)] + ccu_base[8:]
    res_batch = await ccu_service.sync_and_diff_ccus(ccu_batch)
    await event_bus.wait_all()
    assert res_batch["price_changed"] == 0

    ccu_single = ccu_batch[:8] + [
        _upgrade(9, 16123),
        _upgrade(10, 15000, wb=True),
    ]
    res_single = await ccu_service.sync_and_diff_ccus(ccu_single)
    await event_bus.wait_all()
    assert res_single["price_changed"] == 1
    ccu_logs, _ = await ccu_service.list_changelog(event_type="ccu.price_changed")
    assert len(ccu_logs) == 1
    assert ccu_logs[0]["sku_id"] == 5010


async def test_standalone_store_emits_warbond_ship_and_special_sale_events(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    ccu_repo,
    changelog_repo,
    mock_net,
    event_bus: EventBus,
    setting_service,
) -> None:
    """
    验证单船商店上架时，Warbond 整船触发 store.warbond_ship、Best in Show 触发
    store.special_sale，而常规 10 Year/Standard 仅记录 Changelog 不触发上述事件。
    """
    received: list[tuple[EventType, dict[str, Any]]] = []

    async def _on_store_listed(payload: dict[str, Any]) -> None:
        received.append((EventType.STORE_LISTED, payload))

    event_bus.subscribe(EventType.STORE_LISTED, _on_store_listed)

    i18n_service = I18nService(
        settings=isolated_settings,
        net=mock_net,
        event_bus=event_bus,
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        setting_service=setting_service,
    )
    ccu_service = CcuService(
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        ccu_repo=ccu_repo,
        changelog_repo=changelog_repo,
        scraper=RsiScraperClient(net=mock_net),
        i18n_service=i18n_service,
        event_bus=event_bus,
        store_repo=StoreRepository(),
        settings=isolated_settings,
    )

    baseline = [
        ScrapedStandaloneShip(
            sku_id=f"base-{i}",
            matrix_id=f"bm-{i}",
            name=f"Base Ship {i}",
            key=f"base ship {i}",
            price_cents=10000,
            available=True,
            limited=False,
            is_warbond=False,
            store_url=f"/pledge/base-{i}",
            image_url="",
            edition_label="Standard",
        )
        for i in range(1, 5)
    ]
    await ccu_service.sync_standalone_store(baseline)
    await event_bus.wait_all()
    assert received == []

    round2 = baseline + [
        ScrapedStandaloneShip(
            sku_id="sku-wb-ship",
            matrix_id="m-zeus",
            name="Zeus Mk II ES - Warbond",
            key="zeus mk ii es",
            price_cents=16000,
            list_price_cents=17500,
            available=True,
            limited=False,
            is_warbond=True,
            store_url="/pledge/zeus-wb",
            image_url="/media/zeus-wb.jpg",
            edition_label="Warbond",
        ),
        ScrapedStandaloneShip(
            sku_id="sku-bis-ship",
            matrix_id="m-perseus",
            name="Perseus - Best in Show 2956",
            key="perseus",
            price_cents=80000,
            available=True,
            limited=False,
            is_warbond=False,
            store_url="/pledge/perseus-bis",
            image_url="/media/perseus-bis.jpg",
            edition_label="Best in Show",
        ),
        ScrapedStandaloneShip(
            sku_id="sku-10y-ship",
            matrix_id="m-cutlass",
            name="Cutlass Black - 10 Year",
            key="cutlass black",
            price_cents=11000,
            available=True,
            limited=False,
            is_warbond=False,
            store_url="/pledge/cutlass-10y",
            image_url="",
            edition_label="10 Year",
        ),
    ]
    await ccu_service.sync_standalone_store(round2)
    await event_bus.wait_all()

    assert len(received) == 2
    by_sku = {env["payload"]["data"]["sku_id"]: env for _, env in received}

    wb_env = by_sku["sku-wb-ship"]
    assert wb_env["webhook_event"] == "store.warbond_ship"
    assert wb_env["event_tags"] == ["store.warbond_ship"]
    assert wb_env["payload"]["data"]["sale_type"] == "warbond_ship"
    assert (
        wb_env["payload"]["data"]["image_url"]
        == "https://robertsspaceindustries.com/media/zeus-wb.jpg"
    )

    bis_env = by_sku["sku-bis-ship"]
    assert bis_env["webhook_event"] == "store.special_sale"
    assert bis_env["event_tags"] == ["store.special_sale"]
    assert bis_env["payload"]["data"]["is_special_sale"] is True
    assert bis_env["payload"]["data"]["is_warbond"] is False
    assert (
        bis_env["payload"]["data"]["image_url"]
        == "https://robertsspaceindustries.com/media/perseus-bis.jpg"
    )

    store_logs, _ = await ccu_service.list_changelog(event_type="store.new")
    assert len(store_logs) == 3
