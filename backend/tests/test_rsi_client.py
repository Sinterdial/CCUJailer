"""
RSI 抓取客户端单元测试模块。

验证升级商店 GraphQL 在服务端拒绝扩展字段返回 HTTP 400 Bad Request 时能够自动回退，
以及结合 filterShips(fromId=None) 严格过滤历史停售 SKU、仅保留当前真实在售 CCU。
"""

import json

import httpx
import pytest

from cj.scraper.rsi_client import RsiScraperClient, clean_upgrade_edition_label


async def test_fetch_upgrade_store_falls_back_on_400_and_filters_active_skus(
    mock_net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证：
    1. 当 initShipUpgrade 对 validUntil 返回 HTTP 400 时自动回退，且后续轮次记住跳过探测；
    2. 严格结合 filterShips(fromId=None) 过滤掉 initShipUpgrade 中残留的历史停售 SKU，
       同时保留全量舰船 MSRP 与过滤订阅者专享版。

    @param mock_net: 隔离的 NetworkManager 测试夹具
    @param monkeypatch: Pytest 属性替换夹具
    """
    init_queries: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/en/pledge/ships":
            return httpx.Response(
                200,
                text="ok",
                headers={"Set-Cookie": "Rsi-Token=guest-token-123; Path=/"},
            )
        if path in ("/api/account/v2/setAuthToken", "/api/ship-upgrades/setContextToken"):
            assert request.headers.get("x-rsi-token") == "guest-token-123"
            return httpx.Response(200, json={"success": 1})
        if path == "/pledge-store/api/upgrade/graphql":
            body = json.loads(request.content.decode("utf-8"))
            op_name = body[0].get("operationName")
            query_str = body[0]["query"]

            if op_name == "filterShips":
                # 仅 4001（Polaris WB）与 4002（Subscriber，后续应被跳过）当前在售；
                # 历史停售的 4000（Old WB）与 5001（Merchantman 旧价 $350）不在列表中。
                return httpx.Response(
                    200,
                    json=[
                        {
                            "data": {
                                "to": {
                                    "ships": [
                                        {
                                            "id": 116,
                                            "skus": [
                                                {"id": 4001, "price": 93500, "available": True},
                                                {"id": 4002, "price": 80000, "available": True},
                                            ],
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                )

            init_queries.append(query_str)
            if "validUntil" in query_str:
                return httpx.Response(
                    400,
                    json={
                        "errors": [
                            {
                                "message": 'Cannot query field "validUntil" on type "Sku".',
                                "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "data": {
                            "ships": [
                                {
                                    "id": 116,
                                    "name": "Polaris",
                                    "msrp": 97500,
                                    "medias": {"productThumbMediumAndSmall": "/media/polaris.jpg"},
                                    "skus": [
                                        {
                                            "id": 4000,
                                            "title": "Warbond Edition",
                                            "price": 75000,
                                            "available": True,
                                        },
                                        {
                                            "id": 4001,
                                            "title": "Warbond Edition",
                                            "price": 85000,
                                            "available": True,
                                        },
                                        {
                                            "id": 4002,
                                            "title": "Imperator Subscribers Edition",
                                            "price": 80000,
                                            "available": True,
                                        },
                                    ],
                                },
                                {
                                    "id": 36,
                                    "name": "Merchantman",
                                    "msrp": 65000,
                                    "medias": None,
                                    "skus": [
                                        {
                                            "id": 5001,
                                            "title": "Standard Edition",
                                            "price": 35000,
                                            "available": True,
                                        }
                                    ],
                                },
                            ]
                        }
                    }
                ],
            )
        return httpx.Response(404)

    monkeypatch.setattr(mock_net, "_build_proxy_transport", lambda: httpx.MockTransport(_handler))
    scraper = RsiScraperClient(net=mock_net)

    # 1. 首次抓取：经历 400 回退并按 filterShips 过滤历史 SKU
    ships_first = await scraper.fetch_upgrade_store()
    assert len(ships_first) == 2

    polaris = next(s for s in ships_first if s.name == "Polaris")
    merchantman = next(s for s in ships_first if s.name == "Merchantman")

    assert len(polaris.skus) == 1
    assert polaris.skus[0].sku_id == 4001
    assert polaris.skus[0].price_cents == 85000
    assert polaris.skus[0].expires_at is None
    # 停售船仍保留 MSRP 估值（供起点计算），但 skus 为空
    assert merchantman.msrp_cents == 65000
    assert merchantman.skus == []
    assert len(init_queries) == 2

    # 2. 第二次抓取：已记住服务端不支持 validUntil，不再发出扩展探测
    ships_second = await scraper.fetch_upgrade_store()
    assert len(ships_second) == 2
    assert len(init_queries) == 3
    assert "validUntil" not in init_queries[-1]


async def test_fetch_standalone_store_prefers_native_price(
    mock_net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证单船商店抓取（GetBrowseListingQuery）优先使用 nativePrice 税前 USD 美分，
    避免含地区 VAT/GST 的 price 导致 10 Year / Warbond 价格出现非 5 倍数虚高。

    @param mock_net: 隔离的 NetworkManager 测试夹具
    @param monkeypatch: Pytest 属性替换夹具
    """
    saw_native_price = False

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal saw_native_price
        if request.url.path == "/en/pledge/ships":
            return httpx.Response(
                200,
                text="ok",
                headers={"Set-Cookie": "Rsi-Token=guest-tok; Path=/"},
            )
        if request.url.path == "/graphql":
            body = json.loads(request.content.decode("utf-8"))
            query_str = body[0]["query"]
            assert "nativePrice" in query_str
            saw_native_price = True
            products = body[0]["variables"]["query"]["skus"]["products"]
            if "72" in products:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "data": {
                                "store": {
                                    "listing": {
                                        "totalCount": 2,
                                        "resources": [
                                            {
                                                "id": "101",
                                                "name": "Perseus - 10 Year",
                                                "url": "/pledge/ships/perseus/Perseus-IAE",
                                                "price": {"amount": 88000, "discounted": None},
                                                "nativePrice": {
                                                    "amount": 80000,
                                                    "discounted": None,
                                                },
                                                "stock": {"available": True, "unlimited": True},
                                                "isWarbond": False,
                                                "gameItems": [
                                                    {"kind": "SHIP", "shipId": 10},
                                                    {
                                                        "kind": "INSURANCE",
                                                        "name": "10 Years Insurance",
                                                    },
                                                ],
                                            },
                                            {
                                                "id": "102",
                                                "name": "Marauder - Warbond",
                                                "url": "/pledge/ships/marauder/Marauder-WB",
                                                "price": {"amount": 16500, "discounted": 14850},
                                                "nativePrice": {
                                                    "amount": 15000,
                                                    "discounted": 13500,
                                                },
                                                "stock": {"available": True, "unlimited": True},
                                                "isWarbond": True,
                                                "gameItems": [
                                                    {"kind": "SHIP", "shipId": 11},
                                                    {
                                                        "kind": "INSURANCE",
                                                        "name": "Lifetime Insurance",
                                                    },
                                                ],
                                            },
                                        ],
                                    }
                                }
                            }
                        }
                    ],
                )
            return httpx.Response(
                200,
                json=[{"data": {"store": {"listing": {"totalCount": 0, "resources": []}}}}],
            )
        return httpx.Response(404)

    monkeypatch.setattr(mock_net, "_build_proxy_transport", lambda: httpx.MockTransport(_handler))
    scraper = RsiScraperClient(net=mock_net)
    ships = await scraper.fetch_standalone_store()

    assert saw_native_price is True
    assert len(ships) == 2
    assert ships[0].price_cents == 80000
    assert ships[0].list_price_cents == 80000
    assert ships[1].price_cents == 13500
    assert ships[1].list_price_cents == 15000


async def test_upgrade_store_strict_wbccu_excludes_10_year_and_non_wb_bis(
    mock_net,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证严格 WBCCU 分类逻辑：
    - 10 Year Edition 与普通 Best in Show Edition 的 is_warbond 必须为 False；
    - 仅标题明确包含 Warbond 关键字的 SKU（含 Best in Show Warbond Edition）标记为 is_warbond=True。

    @param mock_net: 隔离的 NetworkManager 测试夹具
    @param monkeypatch: Pytest 属性替换夹具
    """
    assert clean_upgrade_edition_label("10 Year Edition") == "10 Year"
    assert clean_upgrade_edition_label("Best in Show 2956 Edition") == "Best in Show"
    assert clean_upgrade_edition_label("Best in Show 2956 Warbond Edition") == "BIS Warbond"
    assert clean_upgrade_edition_label("Warbond Edition") == "Warbond"

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/en/pledge/ships":
            return httpx.Response(
                200,
                text="ok",
                headers={"Set-Cookie": "Rsi-Token=guest-wb-check; Path=/"},
            )
        if path in ("/api/account/v2/setAuthToken", "/api/ship-upgrades/setContextToken"):
            return httpx.Response(200, json={"success": 1})
        if path == "/pledge-store/api/upgrade/graphql":
            body = json.loads(request.content.decode("utf-8"))
            if body[0].get("operationName") == "filterShips":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "data": {
                                "to": {
                                    "ships": [
                                        {
                                            "id": 10,
                                            "skus": [
                                                {"id": 6001, "price": 80000, "available": True},
                                                {"id": 6002, "price": 80000, "available": True},
                                                {"id": 6003, "price": 72000, "available": True},
                                                {"id": 6004, "price": 75000, "available": True},
                                            ],
                                        }
                                    ]
                                }
                            }
                        }
                    ],
                )
            return httpx.Response(
                200,
                json=[
                    {
                        "data": {
                            "ships": [
                                {
                                    "id": 10,
                                    "name": "Perseus",
                                    "msrp": 80000,
                                    "medias": None,
                                    "skus": [
                                        {
                                            "id": 6001,
                                            "title": "10 Year Edition",
                                            "price": 80000,
                                            "available": True,
                                        },
                                        {
                                            "id": 6002,
                                            "title": "Best in Show 2956 Edition",
                                            "price": 80000,
                                            "available": True,
                                        },
                                        {
                                            "id": 6003,
                                            "title": "Best in Show 2956 Warbond Edition",
                                            "price": 72000,
                                            "available": True,
                                        },
                                        {
                                            "id": 6004,
                                            "title": "Warbond Edition",
                                            "price": 75000,
                                            "available": True,
                                        },
                                    ],
                                }
                            ]
                        }
                    }
                ],
            )
        return httpx.Response(404)

    monkeypatch.setattr(mock_net, "_build_proxy_transport", lambda: httpx.MockTransport(_handler))
    scraper = RsiScraperClient(net=mock_net)
    ships = await scraper.fetch_upgrade_store()
    assert len(ships) == 1
    by_id = {sku.sku_id: sku for sku in ships[0].skus}

    assert by_id[6001].edition_label == "10 Year"
    assert by_id[6001].is_warbond is False
    assert by_id[6002].edition_label == "Best in Show"
    assert by_id[6002].is_warbond is False
    assert by_id[6003].edition_label == "BIS Warbond"
    assert by_id[6003].is_warbond is True
    assert by_id[6004].edition_label == "Warbond"
    assert by_id[6004].is_warbond is True
