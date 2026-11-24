"""
Webhook 过滤匹配、多平台模板渲染、退避重试与日志脱敏单元测试模块。
"""

import json
from typing import Any

import httpx

from cj.services.webhook_service import (
    WebhookService,
    build_webhook_payload,
    matches_rule_filters,
)
from tests.conftest import install_mock_transport


def _sample_event_payload() -> dict[str, Any]:
    """
    生成标准 CCU 事件测试负载。

    @return: 标准结构化 Webhook 负载字典
    """
    return {
        "event": "ccu.new_listing",
        "timestamp": "2026-11-23T10:15:00Z",
        "data": {
            "ship_id": "123",
            "sku_id": 4001,
            "ship_name_en": "Zeus Mk II ES",
            "ship_name_zh": "宙斯 Mk II ES",
            "edition_label": "Warbond",
            "is_warbond": True,
            "original_price": 175.0,
            "discounted_price": 160.0,
            "savings": 15.0,
            "currency": "USD",
            "expires_at": "2026-11-24T16:00:00Z",
            "store_url": "https://robertsspaceindustries.com/pledge",
            "image_url": "https://robertsspaceindustries.com/media/zeus-es.jpg",
            "nearest_from_ships": [
                {
                    "name_en": "Prospector",
                    "name_zh": "勘探者",
                    "value": 155.0,
                    "upgrade_cost": 5.0,
                    "always_available": True,
                }
            ],
        },
    }


def test_matches_rule_filters_bilingual_and_ranges() -> None:
    """
    验证 Webhook 过滤逻辑：多条件 AND 交集、双语关键词 OR 匹配以及空条件不限制行为。
    """
    data = _sample_event_payload()["data"]

    # 1. 空条件放行
    assert matches_rule_filters({}, data) is True

    # 2. 中文关键词 OR 匹配 + 价格与立省区间 AND 满足
    rule_filters = {
        "only_warbond": True,
        "ship_keywords": ["Polaris", "宙斯"],
        "min_original_price": 100,
        "max_original_price": 500,
        "min_wb_price": 150,
        "max_wb_price": 200,
        "min_savings": 10,
    }
    assert matches_rule_filters(rule_filters, data) is True

    # 3. 任一边界不满足则拒绝
    assert matches_rule_filters({**rule_filters, "min_savings": 20}, data) is False
    assert matches_rule_filters({**rule_filters, "max_wb_price": 150}, data) is False
    assert matches_rule_filters({**rule_filters, "ship_keywords": ["北极星"]}, data) is False
    assert (
        matches_rule_filters(
            {"only_warbond": True},
            {**data, "is_warbond": False},
        )
        is False
    )


def test_build_webhook_payload_providers() -> None:
    """
    验证 generic、feishu、wecom、discord 与 custom 五种渠道消息格式转换结果。
    """
    standard = _sample_event_payload()

    assert build_webhook_payload("generic", standard) == standard

    feishu = build_webhook_payload("feishu", standard)
    assert feishu["msg_type"] == "interactive"
    assert "宙斯 Mk II ES" in feishu["card"]["header"]["title"]["content"]

    wecom = build_webhook_payload("wecom", standard)
    assert wecom["msgtype"] == "markdown"
    assert "勘探者" in wecom["markdown"]["content"]

    discord = build_webhook_payload("discord", standard)
    assert len(discord["embeds"]) == 1
    assert "Zeus Mk II ES" in discord["embeds"][0]["title"]
    assert discord["embeds"][0]["image"] == {
        "url": "https://robertsspaceindustries.com/media/zeus-es.jpg"
    }

    no_img_payload = {
        **standard,
        "data": {**standard["data"], "image_url": ""},
    }
    discord_no_img = build_webhook_payload("discord", no_img_payload)
    assert "image" not in discord_no_img["embeds"][0]

    custom_tpl = (
        '{"ship":"{{ship_name_zh}}","wb":{{is_warbond}},'
        '"price":{{discounted_price}},"img":"{{image_url}}"}'
    )
    custom = build_webhook_payload("custom", standard, custom_tpl)
    assert custom == {
        "ship": "宙斯 Mk II ES",
        "wb": True,
        "price": 160.0,
        "img": "https://robertsspaceindustries.com/media/zeus-es.jpg",
    }

    # 非法模板安全降级为 standard
    fallback = build_webhook_payload("custom", standard, "{bad-json")
    assert fallback == standard


async def test_webhook_retry_and_secret_redaction(
    isolated_settings,
    migrated_session_factory,
    webhook_repo,
    mock_net,
) -> None:
    """
    验证 Webhook 5xx 重试（最多 3 次）、成功日志记录及 4xx 失败错误信息密钥脱敏。
    """
    attempts_seen: list[str] = []

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        attempts_seen.append(str(request.url))
        if "flaky" in str(request.url):
            if len(attempts_seen) < 3:
                return httpx.Response(503, text="upstream temporarily unavailable")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(400, text="invalid Bearer super-secret-token-9999")

    install_mock_transport(mock_net, _mock_handler)
    service = WebhookService(
        settings=isolated_settings,
        session_factory=migrated_session_factory,
        webhook_repo=webhook_repo,
        net=mock_net,
        retry_base_delay=0.0,
    )

    # 1. 创建前两次 503 第三次 200 的规则，验证重试 3 次后成功落库
    flaky_rule = await service.create_rule(
        {
            "name": "重试测试规则",
            "url": "https://webhook.test/flaky",
            "provider_type": "generic",
            "headers": {"Authorization": "Bearer super-secret-token-9999"},
            "filters": {"only_warbond": True},
        }
    )
    assert flaky_rule["headers"]["Authorization"] == "******9999"

    await service.handle_bus_event(
        {"webhook_event": "ccu.new_listing", "payload": _sample_event_payload()}
    )

    logs, total = await service.list_logs(rule_id=flaky_rule["id"])
    assert total == 1
    assert logs[0]["success"] is True
    assert logs[0]["attempt_count"] == 3
    assert logs[0]["status_code"] == 200

    # 2. 创建返回 400 的规则，验证客户端错误不重试且日志脱敏屏蔽 Token
    bad_rule = await service.create_rule(
        {
            "name": "失败脱敏测试",
            "url": "https://webhook.test/fail",
            "headers": {"Authorization": "Bearer super-secret-token-9999"},
        }
    )
    outcome = await service.send_test_notification(bad_rule["id"])
    assert outcome is not None
    assert outcome.success is False
    assert outcome.attempt_count == 1
    assert outcome.status_code == 400
    assert "super-secret-token-9999" not in outcome.error_message
    assert "******" in outcome.error_message


async def test_webhook_event_type_routing_warbond_ship_upgrade_and_special_sale(
    isolated_settings,
    migrated_session_factory,
    webhook_repo,
    mock_net,
) -> None:
    """
    验证 ccu.warbond_upgrade / store.warbond_ship / store.special_sale 三类订阅事件路由、
    非 Warbond 特殊销售豁免 only_warbond 拦截以及多平台模板渲染表现。
    """
    delivered: list[tuple[str, dict[str, Any]]] = []

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        delivered.append((str(request.url), json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"ok": True})

    install_mock_transport(mock_net, _mock_handler)
    service = WebhookService(
        settings=isolated_settings,
        session_factory=migrated_session_factory,
        webhook_repo=webhook_repo,
        net=mock_net,
        retry_base_delay=0.0,
    )

    await service.create_rule(
        {
            "name": "WB升级规则",
            "url": "https://hook.test/wb-upgrade",
            "events": ["ccu.warbond_upgrade"],
            "filters": {"only_warbond": True},
        }
    )
    await service.create_rule(
        {
            "name": "WB整船规则",
            "url": "https://hook.test/wb-ship",
            "events": ["store.warbond_ship"],
            "filters": {"only_warbond": True},
        }
    )
    await service.create_rule(
        {
            "name": "特殊销售规则",
            "url": "https://hook.test/special",
            "events": ["store.special_sale", "ccu.new_listing"],
            "filters": {"only_warbond": True},
        }
    )

    # 1. Warbond 升级：仅命中 ccu.warbond_upgrade（special 未命中因其 only_warbond 与标签）
    #    注：special 同时订阅了 ccu.new_listing，此处拆测各自独立标签与合并标签行为
    wb_upgrade_payload = _sample_event_payload()
    wb_upgrade_payload["data"]["sale_type"] = "warbond_upgrade"
    await service.handle_bus_event(
        {
            "webhook_event": "ccu.warbond_upgrade",
            "event_tags": ["ccu.warbond_upgrade"],
            "payload": wb_upgrade_payload,
        }
    )
    assert [url for url, _ in delivered] == ["https://hook.test/wb-upgrade"]

    # 2. Warbond 整船：仅命中 store.warbond_ship
    delivered.clear()
    wb_ship_payload = {
        "event": "store.warbond_ship",
        "timestamp": "2026-11-24T12:00:00Z",
        "data": {
            **wb_upgrade_payload["data"],
            "sku_id": "st-zeus-wb",
            "sale_type": "warbond_ship",
            "edition_label": "Warbond",
            "is_warbond": True,
            "is_special_sale": False,
            "nearest_from_ships": [],
        },
    }
    await service.handle_bus_event(
        {
            "webhook_event": "store.warbond_ship",
            "event_tags": ["store.warbond_ship"],
            "payload": wb_ship_payload,
        }
    )
    assert [url for url, _ in delivered] == ["https://hook.test/wb-ship"]

    # 3. 非 Warbond 特殊销售（Best in Show）：即便规则开启 only_warbond=True 仍应放行推送
    delivered.clear()
    bis_payload = {
        "event": "ccu.new_listing",
        "timestamp": "2026-11-24T12:05:00Z",
        "data": {
            **wb_upgrade_payload["data"],
            "ship_name_en": "Perseus",
            "ship_name_zh": "英仙座",
            "edition_label": "Best in Show",
            "sale_type": "special_sale",
            "is_warbond": False,
            "is_special_sale": True,
            "original_price": 800.0,
            "discounted_price": 800.0,
            "savings": 0.0,
            "nearest_from_ships": [],
        },
    }
    await service.handle_bus_event(
        {
            "webhook_event": "ccu.new_listing",
            "event_tags": ["ccu.new_listing", "store.special_sale"],
            "payload": bis_payload,
        }
    )
    assert [url for url, _ in delivered] == ["https://hook.test/special"]

    # 4. 多平台模板渲染校验（store.warbond_ship / store.special_sale / custom 占位符）
    feishu_store = build_webhook_payload("feishu", wb_ship_payload)
    wecom_store = build_webhook_payload("wecom", bis_payload)
    discord_store = build_webhook_payload("discord", wb_ship_payload)
    custom_store = build_webhook_payload(
        "custom",
        wb_ship_payload,
        '{"type":"{{sale_type}}","edition":"{{edition_label}}"}',
    )
    assert feishu_store["msg_type"] == "interactive"
    assert "最近" not in json.dumps(feishu_store, ensure_ascii=False)
    assert wecom_store["msgtype"] == "markdown"
    assert "最近" not in wecom_store["markdown"]["content"]
    assert len(discord_store["embeds"]) == 1
    assert all("最近" not in f["name"] for f in discord_store["embeds"][0]["fields"])
    assert custom_store == {"type": "warbond_ship", "edition": "Warbond"}
