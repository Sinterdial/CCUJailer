"""
全量商店 SKU 聚合、第三方数据源富化与 CCU 路线规划器单元测试模块。
"""

import json

import httpx
import pytest

from cj.repositories.store_repo import StoreRepository
from cj.scraper.enrichment_client import (
    EnrichmentClient,
    parse_cargo_grid_file,
    parse_gold_standard_rows,
    parse_loaners_html,
)
from cj.scraper.rsi_client import (
    RsiScraperClient,
    ScrapedShipMatrixItem,
    ScrapedStandaloneShip,
    ScrapedUpgradeShip,
    ScrapedUpgradeSku,
)
from cj.services.ccu_service import CcuService
from cj.services.i18n_service import I18nService


def test_parsers_for_loaners_gold_and_cargo_grid() -> None:
    """
    验证借用船 HTML 表格、Wiki 金牌标准表格及 sc-cargo.space JSON 网格解析纯函数逻辑。
    """
    html = """
    <table>
      <tr><th>YOUR SHIP</th><th>LOANER</th></tr>
      <tr><td>Polaris</td><td>Hammerhead, Vanguard Harbinger</td></tr>
      <tr><td>600i Explorer and Executive</td><td>325a</td></tr>
    </table>
    """
    rules = parse_loaners_html(html)
    assert len(rules) == 2
    assert rules[0].cover_keys == ["polaris"]
    assert "Hammerhead" in rules[0].loaners
    assert rules[1].cover_keys == ["600i explorer", "600i executive"]

    wiki_pipeline = """
    == List of gold standard vehicles ==
    {| class="wikitable"
    |-
    | [[Gladius]] || Aegis || Implemented || {{Alpha|3.14}}
    |-
    | [[Retaliator]] || Aegis || In Progress || TBD
    |}
    """
    gold = parse_gold_standard_rows(wiki_pipeline)
    assert gold == [("Gladius", "done", "3.14"), ("Retaliator", "in progress", "")]

    grid = parse_cargo_grid_file(
        {
            "name": "Perseus",
            "alternativeNames": ["Perseus [Concept]"],
            "official": {
                "capacity": 96,
                "groups": [
                    {
                        "x": 0,
                        "z": 0,
                        "grids": [{"x": 0, "y": 0, "z": 0, "width": 4, "length": 6, "height": 4}],
                    }
                ],
            },
        }
    )
    assert grid is not None
    assert grid.scu == 96.0
    assert grid.boxes == [[0, 0, 0, 4, 6, 4]]


async def test_store_payload_enrichment_costs_and_plan_map(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    ccu_repo,
    changelog_repo,
    mock_net,
    event_bus,
    setting_service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证全量商店、外部 Mock 富化聚合以及升级补差/路线图接口：
    1. 写入 Ship Matrix 与 10 Year / Best in Show / 游戏内价格 / 货舱 / 售卖标签后，
       get_full_store_payload() 返回全部字段；
    2. get_upgrade_costs(ref) 返回税前 USD 准确补差并缓存；
    3. get_upgrade_plan_map() 返回含估价边在内的规划图数据。
    """
    def _mock_external_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = request.url.host
        if "support.robertsspaceindustries.com" in host:
            return httpx.Response(
                200,
                json={
                    "article": {
                        "body": "<table><tr><th>YOUR SHIP</th><th>LOANER</th></tr>"
                        "<tr><td>Orion</td><td>Prospector, Mole</td></tr></table>"
                    }
                },
            )
        if "starcitizen.tools" in host:
            if "Vehicle_Pipeline" in url:
                return httpx.Response(
                    200,
                    json={
                        "query": {
                            "pages": {
                                "1": {
                                    "revisions": [
                                        {
                                            "slots": {
                                                "main": {
                                                    "*": (
                                                        "List of gold standard vehicles\n"
                                                        "{|\n|-\n| [[Perseus]] || RSI "
                                                        "|| Implemented || {{Alpha|4.4.0}}\n|}"
                                                    )
                                                }
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": {
                            "10": {
                                "title": "Perseus",
                                "revisions": [
                                    {
                                        "slots": {
                                            "main": {
                                                "*": "| addedinversion = Alpha 4.4.0\n"
                                                "| pledgeavailability = Time-limited sales\n"
                                            }
                                        }
                                    }
                                ],
                            }
                        }
                    }
                },
            )
        if "api.star-citizen.wiki" in host:
            if "/items" in request.url.path:
                return httpx.Response(200, json={"data": [], "meta": {"last_page": 1}})
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 200,
                            "uuid": "uuid-perseus",
                            "name": "Perseus",
                            "shipmatrix_name": "Perseus",
                            "class_name": "RSI_Perseus",
                            "version": "4.4.0-live",
                            "insurance": {
                                "claim_time": 24.5,
                                "expedite_time": 8.0,
                                "expedite_cost": 12000,
                            },
                            "skus": [
                                {
                                    "title": "Perseus - Warbond",
                                    "price": 720,
                                    "is_warbond": True,
                                    "source": "upgrade_api",
                                    "imported_at": "2026-11-20",
                                }
                            ],
                            "weaponry": {
                                "fixed_weapons": {"weapons": []},
                                "missiles": {"count": 20},
                            },
                            "turrets": {
                                "manned": [
                                    {
                                        "weapons": [{"name": "Medusa", "size": 8, "dps": 1500}],
                                        "dps_total": 1500,
                                        "sustained_dps_total": 1200,
                                        "alpha_total": 3000,
                                    }
                                ],
                                "remote": [],
                                "pdc": [
                                    {
                                        "weapons": [{"name": "PDC", "size": 1, "dps": 100}],
                                        "dps_total": 100,
                                        "sustained_dps_total": 100,
                                        "alpha_total": 50,
                                    }
                                ],
                            },
                        }
                    ],
                    "meta": {"last_page": 1},
                },
            )
        if "api.uexcorp.space" in host:
            if request.url.path.endswith("/vehicles"):
                return httpx.Response(
                    200, json={"data": [{"id": 1, "uuid": "uuid-perseus", "name": "Perseus"}]}
                )
            if "purchases" in url:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id_vehicle": 1,
                                "vehicle_name": "Perseus",
                                "price_buy": 39700000,
                                "terminal_name": "New Deal - Lorville",
                                "date_modified": 1700000000,
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"data": []})
        if "api.github.com" in host:
            return httpx.Response(
                200,
                json={"sha": "sha-test-1", "tree": [{"path": "src/grids/perseus.json"}]},
            )
        if "raw.githubusercontent.com" in host:
            return httpx.Response(
                200,
                json={
                    "name": "Perseus",
                    "official": {
                        "capacity": 96,
                        "groups": [
                            {
                                "x": 0,
                                "z": 0,
                                "grids": [
                                    {
                                        "x": 0,
                                        "y": 0,
                                        "z": 0,
                                        "width": 4,
                                        "length": 6,
                                        "height": 4,
                                    }
                                ],
                            }
                        ],
                    },
                },
            )
        # Mock RSI 实时升级接口 filterShips(fromId=10)
        if request.url.path == "/en/pledge/ships":
            return httpx.Response(200, text="ok", headers={"Set-Cookie": "Rsi-Token=tok; Path=/"})
        if request.url.path in (
            "/api/account/v2/setAuthToken",
            "/api/ship-upgrades/setContextToken",
        ):
            return httpx.Response(200, json={"success": 1})
        if request.url.path == "/pledge-store/api/upgrade/graphql":
            body = json.loads(request.content.decode("utf-8"))
            assert body[0]["variables"]["fromId"] == 10
            return httpx.Response(
                200,
                json=[
                    {
                        "data": {
                            "to": {
                                "ships": [
                                    {
                                        "id": 200,
                                        "skus": [
                                            {"id": 9001, "upgradePrice": 62000},
                                            {"id": 9002, "upgradePrice": 70000},
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                ],
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(_mock_external_handler)
    monkeypatch.setattr(mock_net, "_proxy_client", httpx.AsyncClient(transport=transport))
    monkeypatch.setattr(mock_net, "_build_proxy_transport", lambda: transport)

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
        enrichment=EnrichmentClient(net=mock_net),
        settings=isolated_settings,
    )

    matrix_items = [
        ScrapedShipMatrixItem(
            matrix_id="10",
            name="Prospector",
            key="prospector",
            manufacturer="MISC",
            manufacturer_code="MISC",
            focus="Mining",
            ship_type="Industrial",
            size="Small",
            min_crew=1,
            max_crew=1,
            cargo_scu=32.0,
            length_m=24.0,
            production_status="flight-ready",
            image_url="",
            store_url="",
        ),
        ScrapedShipMatrixItem(
            matrix_id="200",
            name="Perseus",
            key="perseus",
            manufacturer="Roberts Space Industries",
            manufacturer_code="RSI",
            focus="Heavy Gunship",
            ship_type="Combat",
            size="Large",
            min_crew=4,
            max_crew=6,
            cargo_scu=50.0,
            length_m=100.0,
            production_status="flight-ready",
            image_url="/img/perseus.jpg",
            store_url="https://robertsspaceindustries.com/pledge/ships/perseus/Perseus",
        ),
    ]
    standalone_items = [
        ScrapedStandaloneShip(
            sku_id="s-prospector",
            matrix_id="10",
            name="Prospector - 10 Year",
            key="prospector",
            price_cents=15500,
            available=True,
            limited=False,
            is_warbond=False,
            store_url="https://rsi/prospector",
            image_url="",
            category="ship",
            edition_label="10 Year",
            insurance="10 years",
        ),
        ScrapedStandaloneShip(
            sku_id="s-perseus-10y",
            matrix_id="200",
            name="Perseus - 10 Year",
            key="perseus",
            price_cents=80000,
            available=True,
            limited=False,
            is_warbond=False,
            store_url="https://rsi/perseus-10y",
            image_url="",
            category="ship",
            edition_label="10 Year",
            insurance="10 years",
        ),
        ScrapedStandaloneShip(
            sku_id="s-perseus-bis",
            matrix_id="200",
            name="Perseus - Best in Show 2956",
            key="perseus",
            price_cents=80000,
            available=True,
            limited=False,
            is_warbond=False,
            store_url="https://rsi/perseus-bis",
            image_url="",
            category="ship",
            edition_label="Best in Show",
            insurance="10 years",
            extras=["Perseus Paint"],
        ),
    ]
    upgrade_ships = [
        ScrapedUpgradeShip(
            upgrade_ship_id=10,
            name="Prospector",
            key="prospector",
            msrp_cents=15500,
            image_url="",
            skus=[],
        ),
        ScrapedUpgradeShip(
            upgrade_ship_id=200,
            name="Perseus",
            key="perseus",
            msrp_cents=80000,
            image_url="",
            skus=[
                ScrapedUpgradeSku(
                    sku_id=9001,
                    title="Warbond Edition",
                    edition_label="Warbond",
                    is_warbond=True,
                    price_cents=72000,
                    expires_at=None,
                ),
                ScrapedUpgradeSku(
                    sku_id=9002,
                    title="Standard Edition",
                    edition_label="Standard",
                    is_warbond=False,
                    price_cents=80000,
                    expires_at=None,
                ),
            ],
        ),
    ]

    enrich_res = await ccu_service.sync_catalog_and_enrichment(
        matrix_items=matrix_items,
        standalone_items=standalone_items,
    )
    assert enrich_res["status"] == "ok"
    assert enrich_res["cargo_grids"] >= 1
    await ccu_service.sync_and_diff_ccus(upgrade_ships)

    # 验证 /api/v1/store 聚合结构
    store = await ccu_service.get_full_store_payload()
    assert store["game_version"] == "4.4.0"
    perseus = next(s for s in store["ships"] if s["key"] == "perseus")
    assert perseus["sold"] == "time"
    assert perseus["gold"]["status"] == "done"
    assert perseus["released"] == "4.4.0"
    assert perseus["cargo"] == 96.0
    assert perseus["buy"] == [[39700000, "New Deal - Lorville"]]
    assert perseus["weapons"] is not None
    assert [ed["label"] for ed in perseus["editions"]] == ["10 Year", "Best in Show"]
    assert perseus["editions"][1]["paints"] == 1
    assert len(perseus["upgrades"]) == 2
    assert perseus["upgrades"][0]["nearest_from_ships"][0]["name_en"] == "Prospector"
    assert perseus["upgrades"][0]["nearest_from_ships"][0]["upgrade_cost"] == 565.0
    assert perseus["ccu"] == {"ref": 200, "value": 80000}

    # 验证实时升级补差与税前纠偏（72000 - 15500 = 56500）
    costs_res = await ccu_service.get_upgrade_costs(10)
    assert costs_res is not None
    assert costs_res["costs"]["9001"] == 56500
    assert costs_res["costs"]["9002"] == 64500

    # 验证升级路线规划器映射图
    plan = await ccu_service.get_upgrade_plan_map()
    assert plan["estimated"] is True
    assert "10" in plan["paths"]
    assert plan["paths"]["10"][0] == [9001, 56500]
