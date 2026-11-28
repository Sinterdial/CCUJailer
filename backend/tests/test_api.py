"""
RESTful API v1 端到端集成测试模块。

通过 httpx ASGITransport 验证全站强制登录策略、初始化管理员流程、Webhook Header 脱敏、
系统动态配置热更新、单船中文译名手动覆盖以及 ADMIN_TOKEN 脚本向后兼容行为。
"""

from pathlib import Path

import httpx

from cj.core.config import Settings
from cj.db.models import Changelog, Ship
from main import create_app


async def test_api_auth_boundary_setup_crud_masking_and_override(tmp_path: Path) -> None:
    """
    验证：
    1. 默认全站强制登录时，未认证请求无法读取商店与 CCU 数据（返回 401）；
    2. 首次无用户可通过 /auth/setup 创建管理员并种下会话；
    3. 管理员可完成 Webhook 脱敏存取、设置热更新（含关闭全站强制登录）与舰船人工译名覆盖。
    """
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'api.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. 健康检查公开；未登录时站点内容受默认强制登录保护
            health = await client.get("/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            auth_before = await client.get("/api/v1/auth/status")
            assert auth_before.status_code == 200
            assert auth_before.json()["initialized"] is False
            assert auth_before.json()["auth_required"] is True
            assert auth_before.json()["authenticated"] is False
            assert auth_before.json()["app_version"]
            assert auth_before.json()["github_url"] == "https://github.com/Sinterdial/CCUJailer"

            assert (await client.get("/api/v1/store")).status_code == 401
            assert (await client.get("/api/v1/ccus")).status_code == 401

            # 2. 初始化首个管理员账号（同时返回 JWT 并种下 cj_session Cookie）
            setup_resp = await client.post(
                "/api/v1/auth/setup",
                json={
                    "username": "admin",
                    "password": "StrongPassword123",
                    "display_name": "Admin",
                },
            )
            assert setup_resp.status_code == 201
            assert setup_resp.json()["user"]["role"] == "admin"
            assert "cj_session" in setup_resp.cookies

            auth_after = (await client.get("/api/v1/auth/status")).json()
            assert auth_after["initialized"] is True
            assert auth_after["authenticated"] is True

            # 3. 创建 Webhook 并确认敏感 Header 在响应中脱敏
            create_resp = await client.post(
                "/api/v1/webhooks",
                json={
                    "name": "飞书监控群",
                    "url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                    "provider_type": "feishu",
                    "headers": {"Authorization": "Bearer token-abcd-1234", "X-Tag": "ccu"},
                    "filters": {"only_warbond": True, "ship_keywords": ["宙斯"]},
                },
            )
            assert create_resp.status_code == 201
            created = create_resp.json()
            assert created["headers"]["Authorization"] == "******1234"
            assert created["headers"]["X-Tag"] == "ccu"

            # 4. 更新规则时回传脱敏占位符，后端应自动保留原始明文密钥
            update_resp = await client.put(
                f"/api/v1/webhooks/{created['id']}",
                json={
                    "name": "飞书监控群（已调整）",
                    "headers": {"Authorization": "******1234", "X-Tag": "wb"},
                },
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["headers"]["X-Tag"] == "wb"

            async with app.state.container.session_factory() as db_session:
                stored_rule = await app.state.container.webhook_service._webhook_repo.get_rule(
                    db_session,
                    created["id"],
                )
                ship_row = Ship(key="perseus", name_en="Perseus", name_zh="英仙座")
                db_session.add(ship_row)
                await db_session.commit()
                await db_session.refresh(ship_row)

            assert stored_rule is not None
            assert stored_rule.headers["Authorization"] == "Bearer token-abcd-1234"

            # 5. 手动覆盖舰船中文译名
            ov_resp = await client.put(
                f"/api/v1/ships/{ship_row.id}/i18n",
                json={"name_zh": "珀耳修斯炮艇"},
            )
            assert ov_resp.status_code == 200
            assert ov_resp.json()["name_zh"] == "珀耳修斯炮艇"

            # 6. 全站强制登录仅由环境变量控制，PUT /settings 传入 auth_require_login=False 不生效
            put_cfg = await client.put(
                "/api/v1/settings",
                json={
                    "scrape_interval_seconds": 180,
                    "default_language": "zh",
                    "auth_require_login": False,
                },
            )
            assert put_cfg.status_code == 200
            assert put_cfg.json()["scrape_interval_seconds"] == 180
            assert put_cfg.json()["default_language"] == "zh"
            assert put_cfg.json()["auth_require_login"] is True

            await client.post("/api/v1/auth/logout")
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as anon_client:
                assert (await anon_client.get("/api/v1/store")).status_code == 401
                assert (await anon_client.get("/api/v1/ccus")).status_code == 401
                assert (await anon_client.get("/api/v1/settings")).status_code == 401


async def test_api_auth_require_login_controlled_by_env_only(tmp_path: Path) -> None:
    """
    验证环境变量 AUTH_REQUIRE_LOGIN=false 允许匿名访问公开商店与 CCU 数据，
    管理端点仍受 RBAC 保护，且无法通过 Web UI / API 热修改全站强制登录开关。
    """
    data_dir = tmp_path / "data_env_auth"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'env_auth.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        auth_require_login=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            auth_status = (await client.get("/api/v1/auth/status")).json()
            assert auth_status["auth_required"] is False

            setup_resp = await client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": "StrongPassword123"},
            )
            assert setup_resp.status_code == 201

            # 尝试通过 API 将 auth_require_login 改回 True，应保持只读为 False
            put_resp = await client.put(
                "/api/v1/settings",
                json={"auth_require_login": True},
            )
            assert put_resp.status_code == 200
            assert put_resp.json()["auth_require_login"] is False

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon_client:
            assert (await anon_client.get("/api/v1/store")).status_code == 200
            assert (await anon_client.get("/api/v1/ccus")).status_code == 200
            assert (await anon_client.get("/api/v1/settings")).status_code == 401


async def test_api_admin_token_compat_protection(tmp_path: Path) -> None:
    """
    验证配置静态 ADMIN_TOKEN 后，自动化脚本仍可通过 X-Admin-Token / Bearer 访问受保护端点。
    """
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'auth.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="my-secret-admin-key",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            auth_unauthed = (await client.get("/api/v1/auth/status")).json()
            assert auth_unauthed["authenticated"] is False

            denied = await client.put(
                "/api/v1/settings",
                json={"scrape_interval_seconds": 120},
            )
            assert denied.status_code == 401

            auth_ok = (
                await client.get(
                    "/api/v1/auth/status",
                    headers={"Authorization": "Bearer my-secret-admin-key"},
                )
            ).json()
            assert auth_ok["authenticated"] is True
            assert auth_ok["user"]["role"] == "admin"

            allowed = await client.put(
                "/api/v1/settings",
                json={"scrape_interval_seconds": 120},
                headers={"X-Admin-Token": "my-secret-admin-key"},
            )
            assert allowed.status_code == 200
            assert allowed.json()["scrape_interval_seconds"] == 120


async def test_api_upgrade_planner_feature_toggle(tmp_path: Path) -> None:
    """
    验证升级路线规划功能开关（upgrade_planner_enabled）默认关闭，开启后热生效。
    """
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'planner_toggle.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status_before = (await client.get("/api/v1/auth/status")).json()
            assert status_before["upgrade_planner_enabled"] is False

            setup_resp = await client.post(
                "/api/v1/auth/setup",
                json={
                    "username": "admin",
                    "password": "StrongPassword123",
                    "display_name": "Admin",
                },
            )
            assert setup_resp.status_code == 201

            # 1. 默认关闭时 /api/v1/plan 返回 403
            denied_plan = await client.get("/api/v1/plan")
            assert denied_plan.status_code == 403

            # 2. 通过 /settings 热开启后立即生效并返回 200
            enabled_cfg = await client.put(
                "/api/v1/settings",
                json={"upgrade_planner_enabled": True},
            )
            assert enabled_cfg.status_code == 200
            assert enabled_cfg.json()["upgrade_planner_enabled"] is True

            status_after = (await client.get("/api/v1/auth/status")).json()
            assert status_after["upgrade_planner_enabled"] is True

            allowed_plan = await client.get("/api/v1/plan")
            assert allowed_plan.status_code == 200
            assert "ships" in allowed_plan.json()


async def test_api_default_store_filters_and_passkey_settings(tmp_path: Path) -> None:
    """
    验证管理员可配置全局首页默认筛选/排序及 Passkey 设置，并在 /auth/status 正确下发。
    """
    data_dir = tmp_path / "data_settings"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'settings_v020.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            setup_res = await client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": "StrongPassword123"},
            )
            assert setup_res.status_code == 201

            # 1. 非法筛选键或排序键应返回 422
            bad_cfg = await client.put(
                "/api/v1/settings",
                json={"default_store_filters": ["invalid_key"]},
            )
            assert bad_cfg.status_code == 422

            # 2. 设置默认仅显示仅 WBCCU + 按可飞版本排序 + 自定义 Passkey 参数
            updated = await client.put(
                "/api/v1/settings",
                json={
                    "default_store_filters": ["deals"],
                    "default_store_sort": "released",
                    "passkey_enabled": False,
                    "passkey_rp_name": "MyFleet",
                    "passkey_rp_id": "fleet.example.com",
                    "passkey_origin": "https://fleet.example.com",
                },
            )
            assert updated.status_code == 200
            cfg = updated.json()
            assert cfg["default_store_filters"] == ["deals"]
            assert cfg["default_store_sort"] == "released"
            assert cfg["passkey_enabled"] is False
            assert cfg["passkey_rp_name"] == "MyFleet"
            assert cfg["passkey_rp_id"] == "fleet.example.com"

            # 3. /auth/status 同步体现全局默认与版本号 0.2.0
            auth_st = (await client.get("/api/v1/auth/status")).json()
            assert auth_st["default_store_filters"] == ["deals"]
            assert auth_st["default_store_sort"] == "released"
            assert auth_st["passkey_enabled"] is False
            assert auth_st["app_version"] == "0.2.0"


async def test_api_changelog_wbccu_and_ship_delisted_filters(tmp_path: Path) -> None:
    """
    验证 /api/v1/changelog 对 ccu.wb_delisted（WBCCU 下架）、ccu.standard_delisted
    与 store.gone（整船下架）的精确分类筛选。
    """
    data_dir = tmp_path / "data_changelog"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'changelog_filter.db').as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "no_dist",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with app.state.container.session_factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        Changelog(
                            event_type="ccu.delisted",
                            sku_id=101,
                            ship_name_en="Zeus Mk II ES",
                            ship_name_zh="宙斯 Mk II ES",
                            edition_label="Warbond",
                            is_warbond=True,
                            old_price_cents=16000,
                        ),
                        Changelog(
                            event_type="ccu.delisted",
                            sku_id=102,
                            ship_name_en="Corsair",
                            ship_name_zh="海盗船",
                            edition_label="Standard",
                            is_warbond=False,
                            old_price_cents=25000,
                        ),
                        Changelog(
                            event_type="store.gone",
                            sku_id=0,
                            ship_name_en="Polaris",
                            ship_name_zh="北极星",
                            edition_label="10 Year",
                            is_warbond=False,
                            old_price_cents=97500,
                        ),
                    ]
                )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/setup",
                json={"username": "admin", "password": "StrongPassword123"},
            )

            wb_res = (
                await client.get("/api/v1/changelog", params={"event_type": "ccu.wb_delisted"})
            ).json()
            assert wb_res["total"] == 1
            assert wb_res["items"][0]["ship_name_en"] == "Zeus Mk II ES"

            std_res = (
                await client.get(
                    "/api/v1/changelog", params={"event_type": "ccu.standard_delisted"}
                )
            ).json()
            assert std_res["total"] == 1
            assert std_res["items"][0]["ship_name_en"] == "Corsair"

            gone_res = (
                await client.get("/api/v1/changelog", params={"event_type": "store.gone"})
            ).json()
            assert gone_res["total"] == 1
            assert gone_res["items"][0]["ship_name_en"] == "Polaris"
