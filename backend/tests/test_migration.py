"""
双数据库 Alembic 迁移、SQLite 迁移前自动备份与防降级保护单元测试模块。
"""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from cj.core.config import Settings
from cj.db.migration import (
    BACKEND_ROOT,
    MAX_SQLITE_BACKUPS,
    MigrationDowngradeError,
    _backup_sqlite_database,
    _build_alembic_config,
    run_migrations_async,
)


async def test_sqlite_migration_and_anti_downgrade_guard(
    isolated_settings: Settings,
) -> None:
    """
    验证首次自动建表后，若数据库存在高于代码版本树的未知版本号，迁移将拒绝降级并中止启动。
    """
    await run_migrations_async(isolated_settings)
    db_path = isolated_settings.resolve_sqlite_path()
    assert db_path is not None and db_path.exists()

    # 模拟镜像回退：数据库中存在未知高版本
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '9999_future_schema'")
        conn.commit()

    with pytest.raises(MigrationDowngradeError, match="9999_future_schema"):
        await run_migrations_async(isolated_settings)


async def test_sqlite_pre_migration_backup_retention(isolated_settings: Settings) -> None:
    """
    验证 SQLite 存在待执行迁移时自动快照备份，且最多保留最近 5 份备份文件。
    """
    await run_migrations_async(isolated_settings)
    db_path = isolated_settings.resolve_sqlite_path()
    assert db_path is not None

    backup_dir = isolated_settings.data_dir / "backups"
    for _ in range(7):
        _backup_sqlite_database(db_path, backup_dir)

    backups = list(backup_dir.glob("ccujailer_*.db"))
    assert len(backups) == MAX_SQLITE_BACKUPS


async def test_upgrade_from_0001_to_0002_preserves_existing_data(tmp_path: Path) -> None:
    """
    验证从 0001 初始结构库升级至 0002 时，触发迁移前快照备份、完整创建认证与商店新表且保留旧数据。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "upgrade_test.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
    )

    cfg = _build_alembic_config(settings)
    assert Path(cfg.get_main_option("script_location")).parent == BACKEND_ROOT
    await asyncio.to_thread(command.upgrade, cfg, "0001_initial_schema")

    # 在 0001 表结构下写入一条既有舰船记录
    with closing(sqlite3.connect(str(db_file))) as conn:
        conn.execute(
            """INSERT INTO ships (key, rsi_matrix_id, name_en, name_zh, auto_translated,
               search_index, manufacturer, focus, size, production_status, value_cents,
               always_available, in_store, store_url, image_url, updated_at)
               VALUES ('polaris', 'matrix-polaris', 'Polaris', '北极星', 0, 'polaris',
               'RSI', 'Corvette', 'Capital', 'flight-ready', 97500, 0, 1, '', '',
               '2026-01-01 00:00:00')"""
        )
        conn.commit()

    # 执行迁移至最新 head（0003）
    await run_migrations_async(settings)

    with closing(sqlite3.connect(str(db_file))) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {
            "store_skus",
            "upgrade_paths",
            "users",
            "oidc_states",
            "invite_codes",
        } <= tables
        ship_row = conn.execute(
            "SELECT name_en, pledge_availability, ingame_buy FROM ships WHERE key='polaris'"
        ).fetchone()
        assert ship_row == ("Polaris", "", "[]")

    backups = list((data_dir / "backups").glob("ccujailer_*.db"))
    assert len(backups) == 1


async def test_0004_migration_cleans_abnormal_price_changelogs_and_keeps_valid_rows(
    tmp_path: Path,
) -> None:
    """
    验证从 0003 升级至 0004 时，正确清除孤立非 $5 倍数调价与同秒级 1.1x 批量税率纠偏记录，
    完整保留合法 ccu.listed 与正常 $5 倍数调价记录，并生成迁移前自动备份。
    """
    data_dir = tmp_path / "data_0004"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "cleanup_test.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{db_file.as_posix()}",
        data_dir=data_dir,
        auto_migrate=True,
    )

    cfg = _build_alembic_config(settings)
    await asyncio.to_thread(command.upgrade, cfg, "0003_invite_codes")

    with closing(sqlite3.connect(str(db_file))) as conn:
        # 1. 保留：有效 ccu.listed
        conn.execute(
            """INSERT INTO changelog (occurred_at, event_type, sku_id, ship_name_en,
               ship_name_zh, edition_label, is_warbond, old_price_cents, new_price_cents,
               original_price_cents, details)
               VALUES ('2026-11-23 10:00:00', 'ccu.listed', 3001, 'Nova', '新星',
               'Warbond', 1, NULL, 80000, 90000, '{}')"""
        )
        # 2. 保留：独立有效 $5 整数倍调价
        conn.execute(
            """INSERT INTO changelog (occurred_at, event_type, sku_id, ship_name_en,
               ship_name_zh, edition_label, is_warbond, old_price_cents, new_price_cents,
               original_price_cents, details)
               VALUES ('2026-11-23 11:00:00', 'store.price_changed', 0, 'Polaris', '北极星',
               'Standard', 0, 20000, 18500, 20000, '{}')"""
        )
        # 3. 清除：孤立非 $5 倍数调价
        conn.execute(
            """INSERT INTO changelog (occurred_at, event_type, sku_id, ship_name_en,
               ship_name_zh, edition_label, is_warbond, old_price_cents, new_price_cents,
               original_price_cents, details)
               VALUES ('2026-11-24 10:00:00', 'ccu.price_changed', 3002, 'Bad Tax', '',
               'Standard', 0, 11020, 10000, 11020, '{}')"""
        )
        # 4. 清除：同一秒内 10 条 1.1x 批量税率回落（含 8 条凑巧整除 500 + 2 条非 $5 倍数）
        for idx in range(1, 9):
            conn.execute(
                """INSERT INTO changelog (occurred_at, event_type, sku_id, ship_name_en,
                   ship_name_zh, edition_label, is_warbond, old_price_cents, new_price_cents,
                   original_price_cents, details)
                   VALUES ('2026-11-24 14:16:39', 'store.price_changed', 0, ?, '',
                   'Standard', 0, ?, ?, ?, '{}')""",
                (f"Tax Ship {idx}", 11000 * idx, 10000 * idx, 10000 * idx),
            )
        for idx in range(9, 11):
            conn.execute(
                """INSERT INTO changelog (occurred_at, event_type, sku_id, ship_name_en,
                   ship_name_zh, edition_label, is_warbond, old_price_cents, new_price_cents,
                   original_price_cents, details)
                   VALUES ('2026-11-24 14:16:39', 'store.price_changed', 0, ?, '',
                   'Standard', 0, ?, ?, ?, '{}')""",
                (f"Tax NonStep {idx}", 11550, 10500, 10500),
            )
        conn.commit()

    await run_migrations_async(settings)

    with closing(sqlite3.connect(str(db_file))) as conn:
        ver = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        assert ver == [("0004_cleanup_abnormal_changelogs",)]
        rows = conn.execute(
            """SELECT event_type, old_price_cents, new_price_cents
               FROM changelog ORDER BY id"""
        ).fetchall()
        assert rows == [
            ("ccu.listed", None, 80000),
            ("store.price_changed", 20000, 18500),
        ]

    backups = list((data_dir / "backups").glob("ccujailer_*.db"))
    assert len(backups) == 1
