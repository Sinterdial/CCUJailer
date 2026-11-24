"""
船名中文化词典解析与四级优先级服务单元测试模块。
"""

import json

import httpx

from cj.core.config import SystemConfigKey
from cj.db.models import Ship
from cj.services.i18n_service import I18nService, build_search_index, parse_remote_dictionary
from tests.conftest import install_mock_transport


def test_parse_remote_dictionary_json_and_ini() -> None:
    """
    验证远程词典解析器同时支持 JSON 对象与星际公民 global.ini 格式。
    """
    json_payload = json.dumps({"Polaris": "北极星号", "Zeus Mk II ES": "宙斯 ES"})
    parsed_json = parse_remote_dictionary(json_payload)
    assert parsed_json["Polaris"] == "北极星号"

    ini_payload = """
    ; Star Citizen Localization sample
    vehicle_NameRSI_Polaris=北极星
    vehicle_NameAEGS_Avenger_Titan=复仇者 泰坦
    ui_LabelIgnore=忽略UI键
    """
    parsed_ini = parse_remote_dictionary(ini_payload)
    assert parsed_ini["Polaris"] == "北极星"
    assert parsed_ini["Avenger Titan"] == "复仇者 泰坦"
    assert "ui_LabelIgnore" not in parsed_ini


def test_i18n_priority_override_over_cache_and_builtin(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    mock_net,
    event_bus,
    setting_service,
) -> None:
    """
    验证人工覆盖表 > 远程缓存 > 内置词典 > 家族前缀推导的四级解析优先级。
    """
    override_file = isolated_settings.data_dir / "ship_i18n_override.json"
    cache_file = isolated_settings.data_dir / "ship_i18n_cache.json"

    cache_file.write_text(
        json.dumps({"Polaris": "缓存北极星", "Carrack": "缓存克拉克"}, ensure_ascii=False),
        encoding="utf-8",
    )
    override_file.write_text(
        json.dumps({"Polaris": "自定义北极星"}, ensure_ascii=False),
        encoding="utf-8",
    )

    service = I18nService(
        settings=isolated_settings,
        net=mock_net,
        event_bus=event_bus,
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        setting_service=setting_service,
    )

    # 覆盖表胜过缓存
    res_polaris = service.resolve_local("Polaris")
    assert res_polaris.name_zh == "自定义北极星"
    assert res_polaris.source == "override"

    # 缓存胜过内置词典
    res_carrack = service.resolve_local("Carrack")
    assert res_carrack.name_zh == "缓存克拉克"
    assert res_carrack.source == "cache"

    # 内置精准词典命中
    res_prospector = service.resolve_local("Prospector")
    assert res_prospector.name_zh == "勘探者"
    assert res_prospector.source == "builtin"

    # 家族前缀自动推导
    res_variant = service.resolve_local("Zeus Mk II Prototype")
    assert res_variant.name_zh == "宙斯 Mk II Prototype"
    assert res_variant.source == "prefix"


async def test_sync_remote_dictionary_updates_db_search_index(
    isolated_settings,
    migrated_session_factory,
    ship_repo,
    mock_net,
    event_bus,
    setting_service,
) -> None:
    """
    验证 sync_remote_dictionary 通过代理客户端拉取远程词典后刷新数据库中文名与双语索引。
    """
    async with migrated_session_factory() as session:
        async with session.begin():
            await ship_repo.add(
                session,
                Ship(
                    key="galaxy",
                    name_en="Galaxy",
                    name_zh="",
                    search_index=build_search_index("Galaxy", ""),
                    value_cents=38000,
                ),
            )

    await setting_service.update_settings(
        {SystemConfigKey.I18N_SOURCE_URL: "https://i18n.test/ships.ini"}
    )
    install_mock_transport(
        mock_net,
        lambda _req: httpx.Response(200, text="vehicle_NameRSI_Galaxy=银河号旗舰\n"),
    )

    service = I18nService(
        settings=isolated_settings,
        net=mock_net,
        event_bus=event_bus,
        session_factory=migrated_session_factory,
        ship_repo=ship_repo,
        setting_service=setting_service,
    )

    summary = await service.sync_remote_dictionary()
    assert summary["remote_entries"] == 1
    assert summary["updated_ships"] == 1

    async with migrated_session_factory() as session:
        ship = await ship_repo.get_by_key(session, "galaxy")
        assert ship is not None
        assert ship.name_zh == "银河号旗舰"
        assert "银河号旗舰" in ship.search_index
