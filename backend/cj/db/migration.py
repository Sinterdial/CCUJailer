"""
数据库安全迁移、防降级保护与自动快照备份模块。

支持命令行（python -m cj.db.migration）与 FastAPI 启动前异步调用，兼容 SQLite 与 PostgreSQL：
1. 利用文件锁或 PostgreSQL Advisory Lock 避免多进程并发迁移冲突。
2. 迁移前校验数据库当前 alembic_version 是否存在于代码版本树中，阻止镜像回退引起的静默降级。
3. SQLite 模式下在执行待定迁移前自动通过原生 backup API 备份库文件，默认保留最近 5 份。
"""

import asyncio
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from cj.core.config import Settings
from cj.core.logging import configure_logging, get_logger
from cj.core.security import mask_url_credentials

logger = get_logger(__name__)

PG_MIGRATION_LOCK_ID = 8794001
MAX_SQLITE_BACKUPS = 5
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class MigrationDowngradeError(RuntimeError):
    """
    数据库版本高于当前应用代码版本树时抛出的中止异常。
    """


def _build_alembic_config(settings: Settings) -> Config:
    """
    构建指向 backend/alembic 的 Alembic Config 对象。

    @param settings: 应用静态配置实例
    @return: 已注入目标数据库地址与脚本目录的 Config 实例
    """
    ini_path = BACKEND_ROOT / "alembic.ini"
    alembic_dir = BACKEND_ROOT / "alembic"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["database_url"] = settings.database_url
    return cfg


def _get_revision_state(alembic_cfg: Config) -> tuple[set[str], set[str]]:
    """
    读取当前代码版本树中的全部迁移版本号及最新头部版本号集合。

    @param alembic_cfg: Alembic 配置对象
    @return: (全部已知版本号集合, head 版本号集合)
    """
    script = ScriptDirectory.from_config(alembic_cfg)
    known_revs = {rev.revision for rev in script.walk_revisions()}
    head_revs = set(script.get_heads())
    return known_revs, head_revs


def _verify_no_downgrade(
    current_revs: set[str],
    known_revs: set[str],
) -> None:
    """
    校验数据库当前版本是否处于代码可识别的版本树内，防止版本回退导致结构不匹配。

    @param current_revs: 数据库 alembic_version 表中的版本号集合
    @param known_revs: 当前代码 Alembic 版本树中的已知版本号集合
    @raise MigrationDowngradeError: 当数据库存在未知高版本记录时抛出
    """
    unknown_revs = current_revs - known_revs
    if unknown_revs:
        unknown_str = ", ".join(sorted(unknown_revs))
        logger.error(
            "Database schema revision [%s] is newer or unknown to this build; aborting startup",
            unknown_str,
        )
        raise MigrationDowngradeError(
            f"Refusing to start or downgrade unknown database revision(s): {unknown_str}"
        )


@contextmanager
def _sqlite_file_lock(lock_path: Path) -> Iterator[None]:
    """
    使用跨平台文件锁（Windows msvcrt / POSIX fcntl）保证 SQLite 迁移互斥执行。

    @param lock_path: 锁文件路径
    @return: 上下文迭代器
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            lock_file.write("1")
            lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_sqlite_current_revs(db_path: Path) -> set[str]:
    """
    使用标准库 sqlite3 只读检查现有数据库中的 alembic_version 表版本。

    @param db_path: SQLite 数据库文件路径
    @return: 已存在的版本号集合；若库文件或版本表尚未创建则返回空集合
    """
    if not db_path.exists():
        return set()
    with closing(sqlite3.connect(str(db_path))) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not table_exists:
            return set()
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        return {str(row[0]) for row in rows if row and row[0]}


def _backup_sqlite_database(db_path: Path, backup_dir: Path) -> Path:
    """
    使用 SQLite 原生 online backup API 拍摄一致性快照并清理超限旧备份。

    @param db_path: 源 SQLite 数据库文件路径
    @param backup_dir: 备份输出目录
    @return: 本次生成的备份文件路径
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"ccujailer_{timestamp}.db"

    with closing(sqlite3.connect(str(db_path))) as src_conn:
        with closing(sqlite3.connect(str(backup_path))) as dst_conn:
            src_conn.backup(dst_conn)

    # 仅保留最近的 MAX_SQLITE_BACKUPS 份快照，按文件名时间戳倒序清理
    backups = sorted(backup_dir.glob("ccujailer_*.db"), key=lambda p: p.name, reverse=True)
    for stale in backups[MAX_SQLITE_BACKUPS:]:
        try:
            stale.unlink()
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", stale.name, exc)

    logger.info("Created SQLite pre-migration backup: %s", backup_path.name)
    return backup_path


def _run_sqlite_migration_sync(settings: Settings, alembic_cfg: Config) -> None:
    """
    在同步工作线程中执行 SQLite 加锁、防降级检查、备份及 Alembic 升级。

    @param settings: 应用静态配置实例
    @param alembic_cfg: Alembic 配置对象
    """
    known_revs, head_revs = _get_revision_state(alembic_cfg)
    db_path = settings.resolve_sqlite_path()
    lock_path = settings.data_dir / "migration.lock"

    with _sqlite_file_lock(lock_path):
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            current_revs = _read_sqlite_current_revs(db_path)
            _verify_no_downgrade(current_revs, known_revs)
            if db_path.exists() and current_revs != head_revs:
                _backup_sqlite_database(db_path, settings.data_dir / "backups")

        logger.info("Running Alembic upgrade head (engine=sqlite)")
        command.upgrade(alembic_cfg, "head")


async def _run_postgres_migration_async(settings: Settings, alembic_cfg: Config) -> None:
    """
    使用 PostgreSQL Advisory Lock 保护并发安全并执行防降级检查与 Alembic 升级。

    @param settings: 应用静态配置实例
    @param alembic_cfg: Alembic 配置对象
    """
    known_revs, _ = _get_revision_state(alembic_cfg)
    lock_engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    try:
        async with lock_engine.connect() as conn:
            await conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": PG_MIGRATION_LOCK_ID}
            )
            try:
                current_revs: set[str] = set()
                try:
                    result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                    current_revs = {str(row[0]) for row in result.fetchall() if row and row[0]}
                except SQLAlchemyError:
                    # 首次初始化建表时 alembic_version 尚未存在
                    await conn.rollback()

                _verify_no_downgrade(current_revs, known_revs)
                logger.info(
                    "Running Alembic upgrade head (engine=postgresql, url=%s)",
                    mask_url_credentials(settings.database_url),
                )
                await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": PG_MIGRATION_LOCK_ID},
                )
    finally:
        await lock_engine.dispose()


async def run_migrations_async(settings: Settings) -> None:
    """
    异步执行完整数据库迁移流程（含并发锁、防降级保护与 SQLite 自动备份）。

    @param settings: 应用静态配置实例
    @raise MigrationDowngradeError: 当数据库版本高于当前代码版本时抛出
    """
    alembic_cfg = _build_alembic_config(settings)
    if settings.is_sqlite:
        await asyncio.to_thread(_run_sqlite_migration_sync, settings, alembic_cfg)
    else:
        await _run_postgres_migration_async(settings, alembic_cfg)
    logger.info("Database migration check completed successfully")


def run_migrations(settings: Settings) -> None:
    """
    同步入口：执行数据库迁移，适用于 CLI 或独立启动脚本调用。

    @param settings: 应用静态配置实例
    """
    asyncio.run(run_migrations_async(settings))


def main() -> int:
    """
    命令行执行入口（python -m cj.db.migration）。

    @return: 进程退出码（0 表示成功，1 表示迁移被阻止或失败）
    """
    settings = Settings()
    configure_logging(
        level=settings.log_level,
        log_to_file=settings.log_to_file,
        data_dir=settings.data_dir,
        secrets=(settings.admin_token, settings.translate_api_key),
    )
    try:
        run_migrations(settings)
    except MigrationDowngradeError as exc:
        logger.error("Migration aborted: %s", exc)
        return 1
    except Exception:
        logger.exception("Unexpected database migration failure")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
