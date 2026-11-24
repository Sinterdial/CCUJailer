"""
CCUJailer FastAPI 应用入口与全局生命周期（Lifespan）管理模块。

负责按严格顺序完成配置校验、防降级迁移、连接池初始化、代理客户端启动、
事件总线订阅、调度器托管，并在退出时执行优雅停机与前端静态资源托管。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cj import APP_VERSION
from cj.api.router import router as api_v1_router
from cj.api.schemas import HealthResponse
from cj.core.config import Settings
from cj.core.events import EventBus, EventType
from cj.core.logging import configure_logging, get_logger
from cj.core.net import NetworkManager
from cj.core.scheduler import BackgroundScheduler
from cj.db.engine import create_db_engine, create_session_factory
from cj.db.migration import run_migrations_async
from cj.repositories.ccu_repo import CcuRepository
from cj.repositories.changelog_repo import ChangelogRepository
from cj.repositories.setting_repo import SettingRepository
from cj.repositories.ship_repo import ShipRepository
from cj.repositories.store_repo import StoreRepository
from cj.repositories.user_repo import UserRepository
from cj.repositories.webhook_repo import WebhookRepository
from cj.scraper.enrichment_client import EnrichmentClient
from cj.scraper.rsi_client import RsiScraperClient
from cj.services.auth_service import AuthService
from cj.services.ccu_service import CcuService
from cj.services.i18n_service import I18nService
from cj.services.setting_service import SettingService
from cj.services.webhook_service import WebhookService

logger = get_logger(__name__)


@dataclass(slots=True)
class AppContainer:
    """
    应用运行时依赖容器，挂载于 FastAPI app.state.container。

    @ivar settings: 静态配置实例
    @ivar engine: 异步 SQLAlchemy 引擎
    @ivar session_factory: 异步会话工厂
    @ivar net: 统一出口代理网络管理器
    @ivar event_bus: 进程内异步事件总线
    @ivar scheduler: 后台定时任务调度器
    @ivar setting_service: 动态系统配置服务
    @ivar auth_service: 用户认证、TOTP 与 SSO 业务服务
    @ivar i18n_service: 舰船中文化服务
    @ivar ccu_service: CCU 监控与商店聚合服务
    @ivar webhook_service: Webhook 过滤与投递服务
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    net: NetworkManager
    event_bus: EventBus
    scheduler: BackgroundScheduler
    setting_service: SettingService
    auth_service: AuthService
    i18n_service: I18nService
    ccu_service: CcuService
    webhook_service: WebhookService


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """
    构建并装配完整 FastAPI 应用实例（支持测试传入隔离 Settings）。

    @param custom_settings: 可选的自定义静态配置；省略时从环境变量读取
    @return: 配置完毕的 FastAPI 应用实例
    """
    initial_settings = custom_settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """
        管理应用启停完整生命周期。

        @param app: FastAPI 应用实例
        @return: 异步生命周期上下文迭代器
        """
        settings = custom_settings or Settings()
        # 1. 初始化统一脱敏日志
        configure_logging(
            level=settings.log_level,
            log_to_file=settings.log_to_file,
            data_dir=settings.data_dir,
            secrets=(
                settings.admin_token,
                settings.translate_api_key,
                settings.auth_secret_key,
                settings.init_admin_password,
            ),
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Starting CCUJailer backend service")

        # 2. 启动前执行防降级保护与自动迁移（如开启）
        if settings.auto_migrate:
            await run_migrations_async(settings)

        # 3. 初始化数据库异步连接池与全局代理客户端
        engine = create_db_engine(settings)
        session_factory = create_session_factory(engine)
        net = NetworkManager(settings)
        await net.startup()

        # 4. 组装四层组件与事件订阅
        event_bus = EventBus()
        scheduler = BackgroundScheduler()

        ship_repo = ShipRepository()
        ccu_repo = CcuRepository()
        changelog_repo = ChangelogRepository()
        webhook_repo = WebhookRepository()
        setting_repo = SettingRepository()
        store_repo = StoreRepository()
        user_repo = UserRepository()

        setting_service = SettingService(
            session_factory=session_factory,
            setting_repo=setting_repo,
            changelog_repo=changelog_repo,
            webhook_repo=webhook_repo,
            settings=settings,
        )
        auth_service = AuthService(
            settings=settings,
            session_factory=session_factory,
            user_repo=user_repo,
            setting_service=setting_service,
            net=net,
        )
        await auth_service.ensure_bootstrap_admin()

        i18n_service = I18nService(
            settings=settings,
            net=net,
            event_bus=event_bus,
            session_factory=session_factory,
            ship_repo=ship_repo,
            setting_service=setting_service,
        )
        scraper = RsiScraperClient(net=net)
        enrichment = EnrichmentClient(net=net)
        ccu_service = CcuService(
            session_factory=session_factory,
            ship_repo=ship_repo,
            ccu_repo=ccu_repo,
            changelog_repo=changelog_repo,
            scraper=scraper,
            i18n_service=i18n_service,
            event_bus=event_bus,
            store_repo=store_repo,
            enrichment=enrichment,
            settings=settings,
        )
        webhook_service = WebhookService(
            settings=settings,
            session_factory=session_factory,
            webhook_repo=webhook_repo,
            net=net,
        )

        event_bus.subscribe(EventType.CCU_LISTED, webhook_service.handle_bus_event)
        event_bus.subscribe(EventType.CCU_PRICE_CHANGED, webhook_service.handle_bus_event)
        event_bus.subscribe(EventType.STORE_LISTED, webhook_service.handle_bus_event)
        event_bus.subscribe(EventType.STORE_PRICE_CHANGED, webhook_service.handle_bus_event)

        app.state.container = AppContainer(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            net=net,
            event_bus=event_bus,
            scheduler=scheduler,
            setting_service=setting_service,
            auth_service=auth_service,
            i18n_service=i18n_service,
            ccu_service=ccu_service,
            webhook_service=webhook_service,
        )

        # 5. 启动后台定时任务调度器
        async def _scrape_job() -> None:
            summary = await ccu_service.get_status_summary()
            if summary["total_ships"] == 0:
                await ccu_service.sync_catalog_and_standalone()
            else:
                await ccu_service.sync_standalone_store()
            await ccu_service.sync_and_diff_ccus()

        async def _catalog_and_i18n_job() -> None:
            await ccu_service.sync_catalog_and_enrichment()
            await i18n_service.sync_remote_dictionary()

        async def _cleanup_job() -> None:
            await setting_service.cleanup_expired_logs()

        async def _plan_map_job(is_stopping) -> None:
            snapshot = await setting_service.get_settings()
            if not snapshot.upgrade_planner_enabled:
                return
            await ccu_service.sync_upgrade_paths_map(should_stop=is_stopping)

        if settings.enable_scheduler:
            scheduler.start(
                scrape_job=_scrape_job,
                scrape_interval_provider=setting_service.get_scrape_interval,
                catalog_and_i18n_job=_catalog_and_i18n_job,
                catalog_interval_provider=setting_service.get_catalog_sync_interval,
                cleanup_job=_cleanup_job,
                plan_map_job=_plan_map_job,
            )

        try:
            yield
        finally:
            # 6. 优雅停机：停调度 -> 等在途事件 -> 关 HTTP 客户端 -> 释放 DB 连接池
            logger.info("Shutting down CCUJailer backend service")
            await scheduler.stop(timeout=20.0)
            await event_bus.wait_all(timeout=15.0)
            await net.shutdown()
            await engine.dispose()
            logger.info("CCUJailer shutdown complete")

    application = FastAPI(
        title="CCUJailer API",
        version=APP_VERSION,
        description="Star Citizen CCU 上架监控、双语检索与可编程 Webhook 推送服务",
        lifespan=lifespan,
    )
    application.include_router(api_v1_router)

    @application.get("/healthz", response_model=HealthResponse, tags=["System"])
    async def healthz() -> HealthResponse:
        """
        容器及反向代理探活健康检查端点。

        @return: HealthResponse 状态模型
        """
        return HealthResponse(status="ok")

    # 生产模式下由 FastAPI 托管 Vite 构建的前端静态产物与 SPA 路由回退
    dist_dir = initial_settings.frontend_dist_dir.resolve()
    index_html = dist_dir / "index.html"
    if index_html.is_file():
        assets_dir = dist_dir / "assets"
        if assets_dir.is_dir():
            application.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @application.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            """
            托管前端单页应用静态资源及客户端路由回退。

            @param full_path: 请求路径
            @return: 匹配到的静态文件或 index.html
            @raise HTTPException: 对未命中的 /api 路径返回 404
            """
            if full_path.startswith("api/"):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="API endpoint not found",
                )
            candidate = (dist_dir / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist_dir):
                return FileResponse(str(candidate))
            return FileResponse(str(index_html))

    return application


app = create_app()
