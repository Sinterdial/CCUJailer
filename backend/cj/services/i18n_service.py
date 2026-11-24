"""
舰船中文化与双语检索索引服务模块。

实现四级译名解析优先级：
1. 本地人工覆盖表（data/ship_i18n_override.json）
2. 远程社区汉化词典同步缓存（data/ship_i18n_cache.json）
3. 内置离线全系舰船词典与家族前缀推导（cj/resources/ship_i18n_builtin.json）
4. 可选 OpenAI 兼容接口自动翻译兜底（标记 auto_translated=True）
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cj.core.config import Settings
from cj.core.events import EventBus, EventType
from cj.core.logging import get_logger
from cj.core.net import NetworkManager
from cj.db.models import utc_now
from cj.repositories.ship_repo import ShipRepository
from cj.scraper.rsi_client import normalize_ship_key
from cj.services.setting_service import SettingService

logger = get_logger(__name__)

BUILTIN_DICT_PATH = Path(__file__).resolve().parents[1] / "resources" / "ship_i18n_builtin.json"
_MAKER_PREFIX_RE = re.compile(
    r"^(?:AEGS|ANVL|ARGO|BANU|CNOU|CRUS|DRAK|ESPR|GAMA|KRIG|MISC|MRAI|ORIG|RSI|TMBL|VNCL|XIAN)[_\s]+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """
    舰船译名解析结果对象。

    @ivar name_zh: 解析得到的中文船名（无可用译名时为空字符串）
    @ivar auto_translated: 是否由 AI 兜底自动翻译生成
    @ivar source: 命中来源标识（override/cache/builtin/prefix/ai/none）
    """

    name_zh: str
    auto_translated: bool
    source: str


def build_search_index(name_en: str, name_zh: str, manufacturer: str = "") -> str:
    """
    构造用于模糊搜索的双语检索索引字符串（全小写）。

    @param name_en: 英文船名
    @param name_zh: 中文船名
    @param manufacturer: 制造商名称
    @return: 拼接英文、归一化键、去空格简写、中文名与制造商的检索字符串
    """
    norm_en = normalize_ship_key(name_en)
    compact_en = norm_en.replace(" ", "")
    parts = [name_en.strip().lower(), norm_en, compact_en]
    if name_zh.strip():
        parts.append(name_zh.strip().lower())
        parts.append(name_zh.strip().lower().replace(" ", ""))
    if manufacturer.strip():
        parts.append(manufacturer.strip().lower())
    return " | ".join(dict.fromkeys(p for p in parts if p))


def parse_remote_dictionary(raw_text: str) -> dict[str, str]:
    """
    解析远程汉化词典文本，同时兼容 JSON 映射表与星际公民 global.ini 格式。

    @param raw_text: 远程接口返回的文本内容
    @return: {原始或归一化英文船名: 中文译名} 映射字典
    """
    cleaned = raw_text.lstrip("\ufeff").strip()
    if not cleaned:
        return {}

    # 1. 优先尝试 JSON 结构解析（支持扁平字典、{ships: {...}} 与 [{en, zh}] 列表）
    if cleaned.startswith(("{", "[")):
        try:
            data = json.loads(cleaned)
            return _extract_from_json_payload(data)
        except ValueError:
            pass

    # 2. 回退解析星际公民 global.ini（重点提取 vehicle_Name* 条目及通用键值行）
    mapping: dict[str, str] = {}
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#", "[")):
            continue
        if "=" not in stripped:
            continue
        raw_key, raw_val = stripped.split("=", 1)
        key_part = raw_key.strip()
        val_part = raw_val.strip()
        if not key_part or not val_part:
            continue

        if key_part.lower().startswith("vehicle_name"):
            english_candidate = key_part[len("vehicle_name") :].lstrip("_")
            english_candidate = _MAKER_PREFIX_RE.sub("", english_candidate)
            english_candidate = english_candidate.replace("_", " ").strip()
            if english_candidate:
                mapping[english_candidate] = val_part
        elif not key_part.startswith(("item_", "ui_", "mission_", "hud_")):
            mapping[key_part] = val_part

    return mapping


def _extract_from_json_payload(payload: Any) -> dict[str, str]:
    """
    从多种常见 JSON 汉化词典结构中统一提取英文到中文的映射表。

    @param payload: 已反序列化的 JSON 对象
    @return: {英文名: 中文名} 字典
    """
    mapping: dict[str, str] = {}
    if isinstance(payload, dict):
        if isinstance(payload.get("ships"), dict):
            payload = payload["ships"]
        for key, val in payload.items():
            if isinstance(key, str) and isinstance(val, str) and key.strip() and val.strip():
                mapping[key.strip()] = val.strip()
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            en_val = item.get("name_en") or item.get("en") or item.get("english")
            zh_val = item.get("name_zh") or item.get("zh") or item.get("chinese")
            if (
                isinstance(en_val, str)
                and isinstance(zh_val, str)
                and en_val.strip()
                and zh_val.strip()
            ):
                mapping[en_val.strip()] = zh_val.strip()
    return mapping


def _load_json_dict(path: Path) -> dict[str, str]:
    """
    安全读取本地 JSON 映射表文件；不存在或格式损坏时返回空字典。

    @param path: JSON 文件路径
    @return: 键值对映射字典
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Failed to load i18n JSON file %s: %s", path.name, exc)
        return {}
    return _extract_from_json_payload(raw)


def _normalize_map(raw_map: dict[str, str]) -> dict[str, str]:
    """
    将字典键名统一归一化为标准舰船键，支持大小写及符号容错查找。

    @param raw_map: 原始英文名到中文名映射
    @return: 归一化键名到中文名的字典
    """
    normalized: dict[str, str] = {}
    for k, v in raw_map.items():
        nk = normalize_ship_key(k)
        if nk and v.strip():
            normalized[nk] = v.strip()
    return normalized


class I18nService:
    """
    舰船中文化词典管理与自动同步服务。

    @ivar _settings: 应用静态配置
    @ivar _net: 统一网络管理器
    @ivar _event_bus: 进程内异步事件总线
    @ivar _session_factory: 异步会话工厂
    @ivar _ship_repo: 舰船数据仓库
    @ivar _setting_service: 动态配置服务
    """

    def __init__(
        self,
        settings: Settings,
        net: NetworkManager,
        event_bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        ship_repo: ShipRepository,
        setting_service: SettingService,
    ) -> None:
        """
        初始化中文化服务并预加载离线内置词典、远程缓存与本地覆盖表。

        @param settings: 应用静态配置
        @param net: 统一代理网络管理器
        @param event_bus: 进程内事件总线
        @param session_factory: 异步数据库会话工厂
        @param ship_repo: 舰船数据仓库
        @param setting_service: 动态配置服务
        """
        self._settings = settings
        self._net = net
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._ship_repo = ship_repo
        self._setting_service = setting_service

        self._override_path = self._settings.data_dir / "ship_i18n_override.json"
        self._cache_path = self._settings.data_dir / "ship_i18n_cache.json"

        self._builtin_ships: dict[str, str] = {}
        self._builtin_prefixes: list[tuple[str, str]] = []
        self._always_available_keys: set[str] = set()
        self._cache_map: dict[str, str] = {}
        self._override_map: dict[str, str] = {}

        self._load_builtin_resources()
        self.reload_local_layers()

    def _load_builtin_resources(self) -> None:
        """
        加载项目内置的离线星际公民舰船中英词典与常驻船只基线列表。
        """
        if not BUILTIN_DICT_PATH.exists():
            logger.warning("Builtin i18n resource missing: %s", BUILTIN_DICT_PATH)
            return
        try:
            payload = json.loads(BUILTIN_DICT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load builtin i18n resource: %s", exc)
            return

        self._builtin_ships = _normalize_map(payload.get("ships") or {})
        raw_prefixes = _normalize_map(payload.get("prefixes") or {})
        self._builtin_prefixes = sorted(
            raw_prefixes.items(), key=lambda pair: len(pair[0]), reverse=True
        )
        self._always_available_keys = {
            normalize_ship_key(item)
            for item in (payload.get("always_available_keys") or [])
            if normalize_ship_key(item)
        }

    def reload_local_layers(self) -> None:
        """
        重新加载本地人工覆盖表与远程缓存词典到内存。
        """
        self._override_map = _normalize_map(_load_json_dict(self._override_path))
        self._cache_map = _normalize_map(_load_json_dict(self._cache_path))

    def is_default_always_available(self, key_or_name: str) -> bool:
        """
        判断给定舰船是否在内置基线中属于常驻可买底船。

        @param key_or_name: 归一化键或原始英文船名
        @return: 若属于默认常驻底船返回 True
        """
        return normalize_ship_key(key_or_name) in self._always_available_keys

    def resolve_local(self, name_en: str) -> TranslationResult:
        """
        仅使用本地三级词典（覆盖表 -> 缓存表 -> 内置词典及家族前缀）同步解析中文译名。

        @param name_en: 舰船英文原名
        @return: TranslationResult 解析结果
        """
        key = normalize_ship_key(name_en)
        if not key:
            return TranslationResult(name_zh="", auto_translated=False, source="none")

        # 1. 本地人工覆盖表（最高优先级）
        if key in self._override_map:
            return TranslationResult(
                name_zh=self._override_map[key],
                auto_translated=False,
                source="override",
            )

        # 2. 远程社区汉化词典缓存
        if key in self._cache_map:
            return TranslationResult(
                name_zh=self._cache_map[key],
                auto_translated=False,
                source="cache",
            )

        # 3. 内置离线精准词典
        if key in self._builtin_ships:
            return TranslationResult(
                name_zh=self._builtin_ships[key],
                auto_translated=False,
                source="builtin",
            )

        # 4. 家族前缀推导（例如未收录变体 "Zeus Mk II ST" -> "宙斯 Mk II ST"）
        for prefix_key, prefix_zh in self._builtin_prefixes:
            if key.startswith(f"{prefix_key} "):
                suffix = name_en.strip()[len(prefix_key) :].strip()
                derived = f"{prefix_zh} {suffix}".strip()
                return TranslationResult(
                    name_zh=derived,
                    auto_translated=False,
                    source="prefix",
                )

        return TranslationResult(name_zh="", auto_translated=False, source="none")

    async def resolve_translation(
        self,
        name_en: str,
        allow_ai_fallback: bool = True,
    ) -> TranslationResult:
        """
        按四级流水线解析舰船中文译名，必要时调用配置的 OpenAI 兼容接口进行翻译兜底。

        @param name_en: 舰船英文原名
        @param allow_ai_fallback: 是否允许在本地未命中时请求 AI 自动翻译
        @return: TranslationResult 解析结果
        """
        local_hit = self.resolve_local(name_en)
        if local_hit.name_zh:
            return local_hit

        if not allow_ai_fallback or not self._settings.translate_api_key.strip():
            return local_hit

        dynamic = await self._setting_service.get_settings()
        if not dynamic.translate_enabled:
            return local_hit

        translated = await self._translate_via_openai_compatible(
            name_en=name_en,
            api_base=dynamic.translate_api_base,
            model=dynamic.translate_model,
        )
        if translated:
            return TranslationResult(name_zh=translated, auto_translated=True, source="ai")
        return local_hit

    async def _translate_via_openai_compatible(
        self,
        name_en: str,
        api_base: str,
        model: str,
    ) -> str:
        """
        调用兼容 OpenAI Chat Completions 规范的接口为全新概念船生成初始中文译名。

        @param name_en: 英文船名
        @param api_base: 翻译服务 Base URL
        @param model: 模型名称
        @return: 生成的中文名称，失败时返回空字符串
        """
        endpoint = f"{api_base.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.translate_api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": 40,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You translate Star Citizen spaceship names into concise standard "
                        "Chinese community names. Reply with ONLY the translated ship name."
                    ),
                },
                {"role": "user", "content": name_en},
            ],
        }
        try:
            resp = await self._net.proxy_client.post(
                endpoint, json=payload, headers=headers, timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
            text = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            return text.splitlines()[0].strip().strip("\"'") if text else ""
        except (httpx.HTTPError, ValueError, IndexError, KeyError) as exc:
            logger.warning("AI fallback translation failed for ship='%s': %s", name_en, exc)
            return ""

    async def sync_remote_dictionary(self) -> dict[str, Any]:
        """
        拉取远程汉化词典源（若已配置），更新本地缓存并刷新数据库中全量舰船译名与搜索索引。

        @return: 包含远程下载状态、词典条目数与库内更新舰船数的汇总字典
        """
        self.reload_local_layers()
        dynamic = await self._setting_service.get_settings()
        source_url = dynamic.i18n_source_url.strip()
        remote_fetched = 0

        # 1. 若配置了远程汉化源，经由代理客户端拉取并保存至 cache 文件
        if source_url:
            try:
                resp = await self._net.proxy_client.get(source_url, timeout=30.0)
                resp.raise_for_status()
                parsed = parse_remote_dictionary(resp.text)
                if parsed:
                    self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = self._cache_path.with_suffix(".json.tmp")
                    tmp_path.write_text(
                        json.dumps(parsed, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    tmp_path.replace(self._cache_path)
                    self.reload_local_layers()
                    remote_fetched = len(parsed)
                    logger.info(
                        "Synced remote i18n dictionary (%d entries) from source",
                        remote_fetched,
                    )
                else:
                    logger.warning("Remote i18n dictionary returned 0 valid entries")
            except (httpx.HTTPError, OSError) as exc:
                logger.warning("Failed to fetch remote i18n dictionary: %s", exc)

        # 2. 在显式事务中重算所有舰船中文名与 search_index
        updated_ships = 0
        total_ships = 0
        now = utc_now()
        async with self._session_factory() as session:
            async with session.begin():
                ships = await self._ship_repo.list_all(session)
                total_ships = len(ships)
                for ship in ships:
                    res = self.resolve_local(ship.name_en)
                    # 本地三级词典命中时覆盖译名；未命中时保留已有的 AI 译名
                    target_zh = res.name_zh if res.name_zh else ship.name_zh
                    target_auto = res.auto_translated if res.name_zh else ship.auto_translated
                    new_index = build_search_index(ship.name_en, target_zh, ship.manufacturer)
                    if (
                        ship.name_zh != target_zh
                        or ship.auto_translated != target_auto
                        or ship.search_index != new_index
                    ):
                        ship.name_zh = target_zh
                        ship.auto_translated = target_auto
                        ship.search_index = new_index
                        ship.updated_at = now
                        updated_ships += 1

        # 3. 事务提交成功后广播汉化更新事件
        summary = {
            "remote_entries": remote_fetched,
            "total_ships": total_ships,
            "updated_ships": updated_ships,
        }
        await self._event_bus.publish(EventType.I18N_UPDATED, summary)
        logger.info(
            "I18n refresh finished (total_ships=%d, updated_ships=%d, remote_entries=%d)",
            total_ships,
            updated_ships,
            remote_fetched,
        )
        return summary

    async def set_ship_override(self, ship_id: int, name_zh: str) -> dict[str, Any] | None:
        """
        设置或清除单艘舰船的人工中文译名覆盖，并立即生效至数据库与检索索引。

        @param ship_id: 舰船内部主键 ID
        @param name_zh: 自定义中文译名（传空字符串表示撤销覆盖并恢复默认译名）
        @return: 更新后的舰船摘要字典，舰船不存在时返回 None
        """
        cleaned_zh = name_zh.strip()
        async with self._session_factory() as session:
            async with session.begin():
                ship = await self._ship_repo.get_by_id(session, ship_id)
                if ship is None:
                    return None

                # 1. 更新本地 override JSON 文件（先读再原子写入）
                self._override_path.parent.mkdir(parents=True, exist_ok=True)
                raw_overrides = _load_json_dict(self._override_path)
                target_norm = normalize_ship_key(ship.name_en)
                updated_overrides = {
                    k: v
                    for k, v in raw_overrides.items()
                    if normalize_ship_key(k) != target_norm
                }
                if cleaned_zh:
                    updated_overrides[ship.name_en] = cleaned_zh

                tmp_path = self._override_path.with_suffix(".json.tmp")
                tmp_path.write_text(
                    json.dumps(updated_overrides, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(self._override_path)
                self.reload_local_layers()

                # 2. 重算该船译名并写库
                resolved = self.resolve_local(ship.name_en)
                ship.name_zh = resolved.name_zh
                ship.auto_translated = False
                ship.search_index = build_search_index(
                    ship.name_en, ship.name_zh, ship.manufacturer
                )
                ship.updated_at = utc_now()

                result = {
                    "id": ship.id,
                    "name_en": ship.name_en,
                    "name_zh": ship.name_zh,
                    "auto_translated": ship.auto_translated,
                }

        await self._event_bus.publish(
            EventType.I18N_UPDATED,
            {"updated_ships": 1, "ship_id": ship_id},
        )
        logger.info(
            "Updated manual i18n override for ship_id=%d ('%s' -> '%s')",
            ship_id,
            result["name_en"],
            result["name_zh"],
        )
        return result
