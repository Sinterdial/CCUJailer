"""
Webhook 规则管理、多条件过滤匹配、多平台消息格式渲染与可靠投递服务模块。

作为事件总线订阅者异步消费 ccu.listed 与 ccu.price_changed 事件，
支持标准通用 JSON、飞书卡片、企业微信 Markdown、Discord Embed 与自定义 JSON 模板，
并内置 3 次指数退避重试与脱敏推送日志落库。
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cj.core.config import Settings
from cj.core.logging import get_logger
from cj.core.net import NetworkManager
from cj.core.security import (
    is_masked_value,
    is_sensitive_header,
    mask_headers,
    sanitize_log_text,
)
from cj.db.models import WebhookLog, WebhookRule, ensure_utc, utc_now
from cj.repositories.webhook_repo import WebhookRepository

logger = get_logger(__name__)

DEFAULT_WEBHOOK_EVENTS = ["ccu.new_listing", "ccu.price_changed"]
SUPPORTED_WEBHOOK_EVENTS = (
    "ccu.warbond_upgrade",
    "store.warbond_ship",
    "store.special_sale",
    "ccu.new_listing",
    "ccu.price_changed",
)


class WebhookProviderType(StrEnum):
    """
    支持的 Webhook 消息模板平台类型枚举。
    """

    GENERIC = "generic"
    FEISHU = "feishu"
    WECOM = "wecom"
    DISCORD = "discord"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """
    单次 Webhook 推送执行的汇总结果。

    @ivar rule_id: 规则 ID
    @ivar rule_name: 规则名称
    @ivar success: 是否推送成功（2xx）
    @ivar status_code: 最终 HTTP 状态码或 None
    @ivar attempt_count: 实际尝试次数（1-3）
    @ivar duration_ms: 总耗时（毫秒）
    @ivar error_message: 脱敏后的错误描述（成功时为空字符串）
    """

    rule_id: str
    rule_name: str
    success: bool
    status_code: int | None
    attempt_count: int
    duration_ms: int
    error_message: str


def _is_special_sale_data(event_data: dict[str, Any]) -> bool:
    """
    判断事件数据是否属于 Best in Show / 限量等特殊销售版本。

    @param event_data: 标准事件 data 字典
    @return: 若属于特殊销售返回 True
    """
    if bool(event_data.get("is_special_sale")):
        return True
    edition = str(event_data.get("edition_label") or "").lower()
    return "best in show" in edition or "bis" in edition.split()


def matches_rule_filters(
    filters: dict[str, Any] | None,
    event_data: dict[str, Any],
    *,
    skip_warbond_check: bool = False,
) -> bool:
    """
    依据 Webhook 规则过滤条件校验指定 CCU 事件是否命中推送规则。

    空值或空数组条件视为不限制；全部非空条件按 AND 逻辑求交集，
    ship_keywords 内部按 OR 逻辑大小写不敏感匹配中英文船名。

    @param filters: 规则过滤条件字典
    @param event_data: 标准化 CCU 事件数据体（payload["data"]）
    @param skip_warbond_check: 若规则通过非 Warbond 特殊销售事件命中，则跳过 only_warbond 检查
    @return: 命中所有非空条件返回 True，否则返回 False
    """
    if not filters:
        return True

    # 1. 仅限 Warbond 校验
    if (
        not skip_warbond_check
        and filters.get("only_warbond") is True
        and not bool(event_data.get("is_warbond"))
    ):
        return False

    # 2. 中英双语船名关键词 OR 匹配
    raw_keywords = filters.get("ship_keywords") or []
    keywords = [str(kw).strip().lower() for kw in raw_keywords if str(kw).strip()]
    if keywords:
        name_en = str(event_data.get("ship_name_en") or "").lower()
        name_zh = str(event_data.get("ship_name_zh") or "").lower()
        if not any(kw in name_en or kw in name_zh for kw in keywords):
            return False

    # 3. 原价、优惠购买价与最低立省区间校验
    original_price = float(event_data.get("original_price") or 0.0)
    discounted_price = float(event_data.get("discounted_price") or 0.0)
    savings = float(event_data.get("savings") or 0.0)

    min_orig = filters.get("min_original_price")
    if min_orig is not None and original_price < float(min_orig):
        return False

    max_orig = filters.get("max_original_price")
    if max_orig is not None and original_price > float(max_orig):
        return False

    min_wb = filters.get("min_wb_price")
    if min_wb is not None and discounted_price < float(min_wb):
        return False

    max_wb = filters.get("max_wb_price")
    if max_wb is not None and discounted_price > float(max_wb):
        return False

    min_savings = filters.get("min_savings")
    if min_savings is not None and savings < float(min_savings):
        return False

    return True


def _format_ship_title(data: dict[str, Any]) -> str:
    """
    组装双语舰船名称展示文案。

    @param data: CCU 事件数据字典
    @return: 双语组合船名（无中文译名时返回英文名）
    """
    en = str(data.get("ship_name_en") or "")
    zh = str(data.get("ship_name_zh") or "")
    return f"{zh} ({en})" if zh and zh != en else en


def _format_nearest_lines(nearest: list[dict[str, Any]]) -> str:
    """
    将最近可升级起始船列表格式化为多行可读文本。

    @param nearest: nearest_from_ships 字典列表
    @return: 格式化后的多行起始船说明文本
    """
    if not nearest:
        return "暂无可用起始底船数据"
    lines: list[str] = []
    for item in nearest:
        en = item.get("name_en", "")
        zh = item.get("name_zh", "")
        display = f"{zh} / {en}" if zh and zh != en else en
        tag = "[常驻]" if item.get("always_available") else "[限定]"
        val = item.get("value", 0)
        cost = item.get("upgrade_cost", 0)
        lines.append(f"- {tag} {display} (${val:g}) -> 补差 ${cost:g}")
    return "\n".join(lines)


def build_webhook_payload(
    provider_type: str,
    standard_payload: dict[str, Any],
    custom_template: str | None = None,
) -> dict[str, Any]:
    """
    根据规则配置的渠道类型将标准 CCU 事件转换为目标平台请求体。

    @param provider_type: 渠道类型（generic/feishu/wecom/discord/custom）
    @param standard_payload: 符合设计规范 3.4 的标准事件字典
    @param custom_template: 当 provider_type 为 custom 时使用的 JSON 模板文本
    @return: 可直接 JSON 序列化的目标请求体字典
    """
    provider = (provider_type or WebhookProviderType.GENERIC).lower()
    if provider == WebhookProviderType.GENERIC:
        return standard_payload

    event_name = str(standard_payload.get("event") or "ccu.new_listing")
    timestamp = str(standard_payload.get("timestamp") or "")
    data = standard_payload.get("data") or {}

    ship_display = _format_ship_title(data)
    is_wb = bool(data.get("is_warbond"))
    sale_type = str(data.get("sale_type") or "")
    edition_label = str(data.get("edition_label") or "")
    is_special = bool(data.get("is_special_sale")) or _is_special_sale_data(data)
    if sale_type == "warbond_ship" or event_name == "store.warbond_ship":
        event_badge = (
            "Warbond 整船降价"
            if data.get("store_action") == "store.price_changed"
            else "Warbond 整船上架"
        )
        wb_badge = f"Warbond 整船 · {edition_label}" if edition_label else "Warbond 整船"
    elif sale_type == "special_sale" or event_name == "store.special_sale":
        event_badge = "特殊销售提醒"
        wb_badge = f"特殊销售 · {edition_label}" if edition_label else "特殊销售"
    elif event_name == "ccu.price_changed":
        event_badge = "CCU 降价提醒"
        wb_badge = "Warbond 升级 (WBCCU)" if is_wb else (edition_label or "标准版")
    else:
        event_badge = "Warbond 升级上架" if is_wb else "新 CCU 上架提醒"
        if is_wb and is_special:
            wb_badge = "BIS Warbond 升级"
        elif is_wb:
            wb_badge = "Warbond 升级 (WBCCU)"
        else:
            wb_badge = edition_label or "标准版"

    expires_text = str(data.get("expires_at") or "未公布")
    nearest_list = data.get("nearest_from_ships") or []
    is_store_event = (
        sale_type in ("warbond_ship", "special_sale") or event_name.startswith("store.")
    )
    show_nearest = bool(nearest_list) or not is_store_event
    nearest_text = _format_nearest_lines(nearest_list) if show_nearest else ""
    store_url = str(data.get("store_url") or "https://robertsspaceindustries.com/pledge")
    image_url = str(data.get("image_url") or "").strip()

    # 1. 飞书交互式卡片模板
    if provider == WebhookProviderType.FEISHU:
        nearest_block = f"\n\n**最近可升级起始船推荐**：\n{nearest_text}" if show_nearest else ""
        card_body = (
            f"**目标舰船**：{ship_display}\n"
            f"**版本类型**：{wb_badge}\n"
            f"**价格信息**：原价 ${data.get('original_price', 0):g} | "
            f"现价 **${data.get('discounted_price', 0):g}** | "
            f"立省 **${data.get('savings', 0):g}**\n"
            f"**到期时间**：{expires_text}"
            f"{nearest_block}"
        )
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "template": "orange" if is_wb else ("purple" if is_special else "blue"),
                    "title": {"tag": "plain_text", "content": f"[{event_badge}] {ship_display}"},
                },
                "elements": [
                    {"tag": "markdown", "content": card_body},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "前往 RSI 商店"},
                                "type": "primary",
                                "url": store_url,
                            }
                        ],
                    },
                ],
            },
        }

    # 2. 企业微信群机器人 Markdown 模板
    if provider == WebhookProviderType.WECOM:
        nearest_block = f"\n\n**最近可升级起始船**：\n{nearest_text}" if show_nearest else ""
        md_content = (
            f"### {event_badge}：{ship_display}\n"
            f"> **版本**：{wb_badge}\n"
            f"> **原价**：${data.get('original_price', 0):g} USD\n"
            f'> **现价**：<font color="warning">${data.get("discounted_price", 0):g} USD</font> '
            f"（立省 ${data.get('savings', 0):g}）\n"
            f"> **到期时间**：{expires_text}"
            f"{nearest_block}\n\n"
            f"[前往 RSI 商店]({store_url})"
        )
        return {"msgtype": "markdown", "markdown": {"content": md_content}}

    # 3. Discord Webhook Embed 模板
    if provider == WebhookProviderType.DISCORD:
        fields = [
            {"name": "版本", "value": wb_badge, "inline": True},
            {
                "name": "原价 / 现价",
                "value": (
                    f"${data.get('original_price', 0):g} -> "
                    f"**${data.get('discounted_price', 0):g}**"
                ),
                "inline": True,
            },
            {"name": "立省", "value": f"${data.get('savings', 0):g}", "inline": True},
            {"name": "到期时间", "value": expires_text, "inline": False},
        ]
        if show_nearest:
            fields.append({"name": "最近可升级起始船", "value": nearest_text, "inline": False})
        embed: dict[str, Any] = {
            "title": f"{event_badge} · {ship_display}",
            "url": store_url,
            "color": 0xF59E0B if is_wb else (0x8B5CF6 if is_special else 0x38BDF8),
            "timestamp": timestamp,
            "fields": fields,
            "footer": {"text": "CCUJailer Star Citizen CCU Monitor"},
        }
        if image_url.startswith(("http://", "https://")):
            embed["image"] = {"url": image_url}
        return {"embeds": [embed]}

    # 4. 自定义 JSON 模板渲染（解析异常时安全降级为标准通用 JSON）
    if provider == WebhookProviderType.CUSTOM and custom_template:
        replacements = {
            "{{event}}": event_name,
            "{{timestamp}}": timestamp,
            "{{ship_id}}": str(data.get("ship_id") or ""),
            "{{ship_name_en}}": str(data.get("ship_name_en") or ""),
            "{{ship_name_zh}}": str(data.get("ship_name_zh") or ""),
            "{{edition_label}}": edition_label,
            "{{sale_type}}": sale_type,
            "{{is_warbond}}": "true" if is_wb else "false",
            "{{original_price}}": str(data.get("original_price") or 0),
            "{{discounted_price}}": str(data.get("discounted_price") or 0),
            "{{savings}}": str(data.get("savings") or 0),
            "{{expires_at}}": expires_text,
            "{{store_url}}": store_url,
            "{{image_url}}": image_url,
            "{{nearest_summary}}": nearest_text.replace("\n", "\\n"),
        }
        rendered = custom_template
        for placeholder, val in replacements.items():
            escaped = json.dumps(val, ensure_ascii=False)[1:-1] if "{{" in placeholder else val
            # 对 JSON 内置布尔占位符保持裸布尔值，其余文本走 JSON 转义
            if placeholder == "{{is_warbond}}":
                rendered = rendered.replace(placeholder, val)
            else:
                rendered = rendered.replace(placeholder, escaped)
        try:
            parsed = json.loads(rendered)
            if isinstance(parsed, dict):
                return parsed
        except ValueError as exc:
            logger.warning(
                "Custom webhook template JSON parse failed (%s); falling back to generic", exc
            )

    return standard_payload


class WebhookService:
    """
    Webhook 规则 CRUD、事件过滤消费及重试推送编排服务。

    @ivar _settings: 应用静态配置
    @ivar _session_factory: 异步会话工厂
    @ivar _webhook_repo: Webhook 数据仓库
    @ivar _net: 网络客户端管理器
    @ivar _retry_base_delay: 指数退避基数（秒，测试可设为 0）
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        webhook_repo: WebhookRepository,
        net: NetworkManager,
        retry_base_delay: float = 1.0,
    ) -> None:
        """
        初始化 Webhook 服务。

        @param settings: 应用静态配置
        @param session_factory: 异步数据库会话工厂
        @param webhook_repo: Webhook Repository
        @param net: 网络客户端管理器
        @param retry_base_delay: 重试指数退避基础秒数，默认 1.0
        """
        self._settings = settings
        self._session_factory = session_factory
        self._webhook_repo = webhook_repo
        self._net = net
        self._retry_base_delay = retry_base_delay

    def _serialize_rule(self, rule: WebhookRule) -> dict[str, Any]:
        """
        将 WebhookRule ORM 实体序列化为对前端安全脱敏的字典结构。

        @param rule: WebhookRule 数据库实体
        @return: 敏感 Header 已脱敏的规则字典
        """
        return {
            "id": rule.id,
            "name": rule.name,
            "enabled": rule.enabled,
            "url": rule.url,
            "method": rule.method,
            "provider_type": rule.provider_type,
            "custom_template": rule.custom_template,
            "headers": mask_headers(rule.headers),
            "events": list(rule.events or DEFAULT_WEBHOOK_EVENTS),
            "filters": dict(rule.filters or {}),
            "created_at": ensure_utc(rule.created_at),
            "updated_at": ensure_utc(rule.updated_at),
        }

    async def list_rules(self) -> list[dict[str, Any]]:
        """
        获取全部已配置的 Webhook 规则（敏感 Header 自动脱敏）。

        @return: 规则字典列表
        """
        async with self._session_factory() as session:
            rules = await self._webhook_repo.list_rules(session)
        return [self._serialize_rule(r) for r in rules]

    async def create_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        创建一条新的 Webhook 推送规则并持久化。

        @param data: 经过接口校验的规则创建参数字典
        @return: 创建完成且 Header 脱敏后的规则字典
        """
        rule_id = str(data.get("id") or f"rule_{uuid.uuid4().hex[:10]}")
        now = utc_now()
        rule = WebhookRule(
            id=rule_id,
            name=str(data["name"]).strip(),
            enabled=bool(data.get("enabled", True)),
            url=str(data["url"]).strip(),
            method=str(data.get("method") or "POST").upper(),
            provider_type=str(data.get("provider_type") or WebhookProviderType.GENERIC),
            custom_template=data.get("custom_template"),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
            events=list(data.get("events") or DEFAULT_WEBHOOK_EVENTS),
            filters=dict(data.get("filters") or {}),
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            async with session.begin():
                await self._webhook_repo.add_rule(session, rule)

        logger.info("Created webhook rule id=%s name='%s'", rule.id, rule.name)
        return self._serialize_rule(rule)

    async def update_rule(self, rule_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """
        修改指定的 Webhook 规则；若传入的敏感 Header 值为掩码占位符则保留库中原密钥。

        @param rule_id: 规则唯一 ID
        @param data: 待更新字段字典
        @return: 更新后的规则字典；规则不存在时返回 None
        """
        async with self._session_factory() as session:
            async with session.begin():
                rule = await self._webhook_repo.get_rule(session, rule_id)
                if rule is None:
                    return None

                existing_headers = dict(rule.headers or {})
                incoming_headers = data.get("headers")
                if incoming_headers is not None:
                    merged_headers: dict[str, str] = {}
                    for k, v in incoming_headers.items():
                        key_str, val_str = str(k), str(v)
                        if (
                            is_sensitive_header(key_str)
                            and is_masked_value(val_str)
                            and key_str in existing_headers
                        ):
                            merged_headers[key_str] = existing_headers[key_str]
                        else:
                            merged_headers[key_str] = val_str
                    rule.headers = merged_headers

                if "name" in data and data["name"] is not None:
                    rule.name = str(data["name"]).strip()
                if "enabled" in data and data["enabled"] is not None:
                    rule.enabled = bool(data["enabled"])
                if "url" in data and data["url"] is not None:
                    rule.url = str(data["url"]).strip()
                if "method" in data and data["method"] is not None:
                    rule.method = str(data["method"]).upper()
                if "provider_type" in data and data["provider_type"] is not None:
                    rule.provider_type = str(data["provider_type"])
                if "custom_template" in data:
                    rule.custom_template = data["custom_template"]
                if "events" in data and data["events"] is not None:
                    rule.events = list(data["events"])
                if "filters" in data and data["filters"] is not None:
                    rule.filters = dict(data["filters"])
                rule.updated_at = utc_now()

        logger.info("Updated webhook rule id=%s", rule_id)
        return self._serialize_rule(rule)

    async def delete_rule(self, rule_id: str) -> bool:
        """
        删除指定的 Webhook 规则。

        @param rule_id: 待删除规则 ID
        @return: 成功删除返回 True，未找到规则返回 False
        """
        async with self._session_factory() as session:
            async with session.begin():
                rule = await self._webhook_repo.get_rule(session, rule_id)
                if rule is None:
                    return False
                await self._webhook_repo.delete_rule(session, rule)

        logger.info("Deleted webhook rule id=%s", rule_id)
        return True

    async def list_logs(
        self,
        limit: int = 50,
        offset: int = 0,
        rule_id: str | None = None,
        success: bool | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        分页查询 Webhook 历史投递日志。

        @param limit: 单页条数
        @param offset: 偏移量
        @param rule_id: 按规则 ID 筛选
        @param success: 按成功状态筛选
        @return: (日志字典列表, 总匹配条数)
        """
        async with self._session_factory() as session:
            logs, total = await self._webhook_repo.list_logs(
                session=session,
                limit=limit,
                offset=offset,
                rule_id=rule_id,
                success=success,
            )

        items = [
            {
                "id": entry.id,
                "rule_id": entry.rule_id,
                "rule_name": entry.rule_name,
                "event_type": entry.event_type,
                "ship_name": entry.ship_name,
                "status_code": entry.status_code,
                "success": entry.success,
                "attempt_count": entry.attempt_count,
                "duration_ms": entry.duration_ms,
                "error_message": entry.error_message,
                "created_at": ensure_utc(entry.created_at),
            }
            for entry in logs
        ]
        return items, total

    async def handle_bus_event(self, envelope: dict[str, Any]) -> None:
        """
        进程内 EventBus 消费入口：接收商店与 CCU 领域事件并触发所有匹配规则的异步推送。

        @param envelope: EventBus 下发的事件信封（含 webhook_event、event_tags 与 payload）
        """
        webhook_event = str(envelope.get("webhook_event") or "ccu.new_listing")
        payload = envelope.get("payload") or {}
        event_data = payload.get("data") or {}
        raw_tags = envelope.get("event_tags")
        if raw_tags:
            event_tags = list(dict.fromkeys(str(tag) for tag in raw_tags if tag))
        else:
            event_tags = [webhook_event]
            if webhook_event in ("ccu.new_listing", "ccu.price_changed"):
                if bool(event_data.get("is_warbond")):
                    event_tags.append("ccu.warbond_upgrade")
                if _is_special_sale_data(event_data):
                    event_tags.append("store.special_sale")

        async with self._session_factory() as session:
            rules = await self._webhook_repo.list_rules(session, enabled_only=True)

        matched_rules: list[WebhookRule] = []
        for rule in rules:
            subscribed_events = rule.events or DEFAULT_WEBHOOK_EVENTS
            matched_keys = [tag for tag in event_tags if tag in subscribed_events]
            if not matched_keys:
                logger.debug(
                    "Webhook rule id=%s skipped (events=%s not subscribed)",
                    rule.id,
                    event_tags,
                )
                continue
            skip_wb = "store.special_sale" in matched_keys and not bool(
                event_data.get("is_warbond")
            )
            if not matches_rule_filters(rule.filters, event_data, skip_warbond_check=skip_wb):
                logger.debug(
                    "Webhook rule id=%s skipped (filter mismatch for ship='%s')",
                    rule.id,
                    event_data.get("ship_name_en", ""),
                )
                continue
            matched_rules.append(rule)

        if not matched_rules:
            return

        await asyncio.gather(
            *(self._deliver_and_log(rule, webhook_event, payload) for rule in matched_rules)
        )

    async def send_test_notification(self, rule_id: str) -> DeliveryOutcome | None:
        """
        向指定 Webhook 规则发送一条模拟宙斯 Mk II ES Warbond CCU 测试通知。

        @param rule_id: 目标规则 ID
        @return: DeliveryOutcome 投递结果；当规则不存在时返回 None
        """
        async with self._session_factory() as session:
            rule = await self._webhook_repo.get_rule(session, rule_id)
        if rule is None:
            return None

        now_iso = utc_now().isoformat().replace("+00:00", "Z")
        sample_payload: dict[str, Any] = {
            "event": "ccu.new_listing",
            "timestamp": now_iso,
            "data": {
                "ship_id": "test-zeus-es",
                "sku_id": 999001,
                "ship_name_en": "Zeus Mk II ES",
                "ship_name_zh": "宙斯 Mk II ES",
                "edition_label": "Warbond",
                "sale_type": "warbond_upgrade",
                "is_warbond": True,
                "is_special_sale": False,
                "original_price": 175.0,
                "discounted_price": 160.0,
                "previous_price": None,
                "savings": 15.0,
                "currency": "USD",
                "expires_at": None,
                "store_url": "https://robertsspaceindustries.com/pledge",
                "image_url": (
                    "https://robertsspaceindustries.com/media/zeus-mk-ii-es/store_small.jpg"
                ),
                "nearest_from_ships": [
                    {
                        "name_en": "Prospector",
                        "name_zh": "勘探者",
                        "value": 155.0,
                        "upgrade_cost": 5.0,
                        "always_available": True,
                    },
                    {
                        "name_en": "Cutlass Red",
                        "name_zh": "黑弯刀 红",
                        "value": 135.0,
                        "upgrade_cost": 25.0,
                        "always_available": True,
                    },
                ],
            },
        }
        return await self._deliver_and_log(rule, "ccu.test", sample_payload)

    async def _deliver_and_log(
        self,
        rule: WebhookRule,
        event_type: str,
        standard_payload: dict[str, Any],
    ) -> DeliveryOutcome:
        """
        带最多 3 次指数退避重试的 Webhook 发送核心逻辑，并将最终脱敏结果持久化至日志表。

        @param rule: 目标 WebhookRule 实体
        @param event_type: 触发事件类型名称
        @param standard_payload: 标准 CCU 事件负载字典
        @return: DeliveryOutcome 投递结果对象
        """
        data = standard_payload.get("data") or {}
        ship_name = _format_ship_title(data)
        body = build_webhook_payload(
            provider_type=rule.provider_type,
            standard_payload=standard_payload,
            custom_template=rule.custom_template,
        )
        headers = {"Content-Type": "application/json", **(rule.headers or {})}
        extra_secrets = tuple(
            v
            for k, v in headers.items()
            if is_sensitive_header(k) and isinstance(v, str) and len(v) >= 4
        ) + (self._settings.admin_token, self._settings.translate_api_key)

        max_attempts = 3
        started_at = time.monotonic()
        status_code: int | None = None
        success = False
        error_message = ""
        attempts = 0

        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                response = await self._net.webhook_client.request(
                    method=(rule.method or "POST").upper(),
                    url=rule.url,
                    json=body,
                    headers=headers,
                )
                status_code = response.status_code
                if 200 <= status_code < 300:
                    success = True
                    error_message = ""
                    break

                snippet = sanitize_log_text(response.text[:300], extra_secrets)
                error_message = f"HTTP {status_code}: {snippet}".strip()
                retryable = status_code >= 500 or status_code == 429
                if not retryable or attempt == max_attempts:
                    break
                logger.warning(
                    "Webhook delivery retryable failure (rule=%s, attempt=%d/%d, status=%d)",
                    rule.id,
                    attempt,
                    max_attempts,
                    status_code,
                )
            except httpx.HTTPError as exc:
                error_message = sanitize_log_text(f"{type(exc).__name__}: {exc}", extra_secrets)
                if attempt == max_attempts:
                    break
                logger.warning(
                    "Webhook delivery error (rule=%s, attempt=%d/%d): %s",
                    rule.id,
                    attempt,
                    max_attempts,
                    error_message,
                )

            if self._retry_base_delay > 0:
                await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))

        duration_ms = int((time.monotonic() - started_at) * 1000)

        if success:
            logger.info(
                "Webhook delivered (rule=%s, event=%s, ship='%s', status=%s, "
                "attempts=%d, duration_ms=%d)",
                rule.id,
                event_type,
                ship_name,
                status_code,
                attempts,
                duration_ms,
            )
        else:
            logger.error(
                "Webhook delivery exhausted retries (rule=%s, event=%s, ship='%s', "
                "status=%s, duration_ms=%d, error=%s)",
                rule.id,
                event_type,
                ship_name,
                status_code,
                duration_ms,
                error_message,
            )

        async with self._session_factory() as session:
            async with session.begin():
                await self._webhook_repo.add_log(
                    session,
                    WebhookLog(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        event_type=event_type,
                        ship_name=ship_name[:200],
                        status_code=status_code,
                        success=success,
                        attempt_count=attempts,
                        duration_ms=duration_ms,
                        error_message=error_message,
                        created_at=utc_now(),
                    ),
                )

        return DeliveryOutcome(
            rule_id=rule.id,
            rule_name=rule.name,
            success=success,
            status_code=status_code,
            attempt_count=attempts,
            duration_ms=duration_ms,
            error_message=error_message,
        )
