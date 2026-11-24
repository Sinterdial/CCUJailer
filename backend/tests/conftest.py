"""
Pytest 公共测试夹具模块。

提供临时 SQLite 目录配置、内存异步数据库引擎、事件总线及 Mock 网络管理器。
"""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from cj.core.config import Settings
from cj.core.events import EventBus
from cj.core.net import NetworkManager
from cj.db.engine import create_db_engine, create_session_factory
from cj.db.migration import run_migrations_async
from cj.repositories.ccu_repo import CcuRepository
from cj.repositories.changelog_repo import ChangelogRepository
from cj.repositories.setting_repo import SettingRepository
from cj.repositories.ship_repo import ShipRepository
from cj.repositories.webhook_repo import WebhookRepository
from cj.services.setting_service import SettingService


@pytest.fixture
def isolated_settings(tmp_path: Path) -> Settings:
    """
    构造完全隔离于 tmp_path 临时目录的 Settings 实例。

    @param tmp_path: Pytest 临时路径夹具
    @return: 隔离的 Settings 配置对象
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "test_ccujailer.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
    return Settings(
        _env_file=None,
        database_url=db_url,
        data_dir=data_dir,
        auto_migrate=True,
        enable_scheduler=False,
        admin_token="",
        frontend_dist_dir=tmp_path / "nonexistent_dist",
    )


@pytest.fixture
async def migrated_session_factory(isolated_settings: Settings):
    """
    在临时 SQLite 数据库上执行完整 Alembic 迁移并返回异步会话工厂。

    @param isolated_settings: 隔离的 Settings 配置夹具
    @return: 异步会话工厂迭代器，测试结束后自动释放连接池
    """
    await run_migrations_async(isolated_settings)
    engine = create_db_engine(isolated_settings)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def event_bus() -> EventBus:
    """
    创建独立的进程内异步事件总线实例。

    @return: EventBus 实例
    """
    return EventBus()


@pytest.fixture
async def mock_net(isolated_settings: Settings) -> AsyncIterator[NetworkManager]:
    """
    创建不触发真实外网调用的可替换传输网络管理器。

    @param isolated_settings: 隔离的 Settings 配置夹具
    @return: NetworkManager 异步迭代器
    """
    net = NetworkManager(isolated_settings)
    await net.startup()
    try:
        yield net
    finally:
        await net.shutdown()


@pytest.fixture
def setting_service(migrated_session_factory) -> SettingService:
    """
    构造绑定临时数据库的 SettingService 实例。

    @param migrated_session_factory: 已迁移的异步会话工厂
    @return: SettingService 实例
    """
    return SettingService(
        session_factory=migrated_session_factory,
        setting_repo=SettingRepository(),
        changelog_repo=ChangelogRepository(),
        webhook_repo=WebhookRepository(),
    )


@pytest.fixture
def ship_repo() -> ShipRepository:
    """
    提供 ShipRepository 实例。

    @return: ShipRepository 实例
    """
    return ShipRepository()


@pytest.fixture
def ccu_repo() -> CcuRepository:
    """
    提供 CcuRepository 实例。

    @return: CcuRepository 实例
    """
    return CcuRepository()


@pytest.fixture
def changelog_repo() -> ChangelogRepository:
    """
    提供 ChangelogRepository 实例。

    @return: ChangelogRepository 实例
    """
    return ChangelogRepository()


@pytest.fixture
def webhook_repo() -> WebhookRepository:
    """
    提供 WebhookRepository 实例。

    @return: WebhookRepository 实例
    """
    return WebhookRepository()


def install_mock_transport(
    net: NetworkManager,
    handler,
) -> None:
    """
    将 NetworkManager 的 proxy_client 与 webhook_client 替换为 httpx.MockTransport。

    @param net: 目标 NetworkManager 实例
    @param handler: MockTransport 请求回调处理函数
    """
    transport = httpx.MockTransport(handler)
    net._proxy_client = httpx.AsyncClient(transport=transport)
    net._webhook_client = httpx.AsyncClient(transport=transport)
