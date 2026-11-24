"""
双引擎异步数据库连接池与会话工厂模块。

自动识别 SQLite（aiosqlite）与 PostgreSQL（asyncpg），在连接建立时配置 SQLite WAL 模式、
外键约束与忙等待超时，并在 PostgreSQL 模式下启用连接池探活。
"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cj.core.config import Settings
from cj.core.logging import get_logger
from cj.core.security import mask_url_credentials

logger = get_logger(__name__)


def create_db_engine(settings: Settings) -> AsyncEngine:
    """
    依据静态配置创建适配当前数据库方言的 SQLAlchemy 异步引擎。

    @param settings: 应用静态配置实例
    @return: 配置完毕的 AsyncEngine 实例
    """
    # 1. SQLite 模式下提前创建本地数据目录并关闭同线程检查限制
    engine_kwargs: dict[str, Any] = {"echo": False}
    if settings.is_sqlite:
        sqlite_path = settings.resolve_sqlite_path()
        if sqlite_path is not None:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # 2. PostgreSQL 模式下配置连接探活与池容量
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    engine = create_async_engine(settings.database_url, **engine_kwargs)

    # 3. 为每个 SQLite 底层连接强制开启 WAL 日志、外键约束与忙锁超时
    if settings.is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA busy_timeout=5000;")
            cursor.close()

    logger.info(
        "Database engine initialized (url=%s)",
        mask_url_credentials(settings.database_url),
    )
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    基于给定异步引擎构造 SQLAlchemy AsyncSession 工厂。

    @param engine: 已初始化的 AsyncEngine 实例
    @return: expire_on_commit=False 的异步会话工厂
    """
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
