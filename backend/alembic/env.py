"""
Alembic 异步数据库迁移环境配置脚本。

强制启用 render_as_batch=True 以兼容 SQLite 的 ALTER TABLE 操作，并同时支持
sqlite+aiosqlite 与 postgresql+asyncpg 双引擎迁移执行。
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from cj.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """
    解析本次迁移使用的数据库连接地址（优先读取运行时注入配置，其次读取 DATABASE_URL）。

    @return: 数据库连接字符串
    """
    attr_url = config.attributes.get("database_url")
    if isinstance(attr_url, str) and attr_url.strip():
        return attr_url.strip()
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url.strip()
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    """
    以离线模式生成 SQL 迁移脚本（不连接数据库）。
    """
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    在给定同步连接上下文中配置并执行 Alembic 迁移。

    @param connection: SQLAlchemy 同步连接适配器对象
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    使用异步引擎建立连接并派发迁移任务。
    """
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """
    以在线模式执行迁移；若外部已注入同步连接则直接复用，否则启动异步迁移协程。
    """
    provided_connection = config.attributes.get("connection")
    if provided_connection is not None:
        do_run_migrations(provided_connection)
        return

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
