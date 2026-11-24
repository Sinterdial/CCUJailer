"""
单船商店 SKU 与升级路线可达图数据访问层（Repository）。

封装 store_skus 表和 upgrade_paths 表的数据库读写，不直接提交或回滚事务。
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import StoreSku, UpgradePath


class StoreRepository:
    """
    单船商店版本与升级路线表 ORM 仓库。
    """

    async def list_all_store_skus(self, session: AsyncSession) -> list[StoreSku]:
        """
        查询全部单船商店及模块 SKU 记录。

        @param session: 调用方提供的异步数据库会话
        @return: StoreSku 实体列表
        """
        stmt = select(StoreSku).order_by(StoreSku.price_cents.asc(), StoreSku.sku_id.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def add_store_sku(self, session: AsyncSession, sku: StoreSku) -> StoreSku:
        """
        将新建的 StoreSku 实体加入会话并 flush。

        @param session: 异步数据库会话
        @param sku: 待写入的 StoreSku 实例
        @return: 已加入会话的 StoreSku 实例
        """
        session.add(sku)
        await session.flush()
        return sku

    async def list_all_upgrade_paths(self, session: AsyncSession) -> list[UpgradePath]:
        """
        读取全部已缓存的升级路线可达边。

        @param session: 异步数据库会话
        @return: UpgradePath 实体列表
        """
        stmt = select(UpgradePath).order_by(
            UpgradePath.from_upgrade_ship_id.asc(),
            UpgradePath.cost_cents.asc(),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def replace_upgrade_paths_for_ships(
        self,
        session: AsyncSession,
        paths_by_from: dict[int, dict[int, int]],
        updated_at: datetime,
    ) -> int:
        """
        批量替换指定起始船集合对应的所有升级可达边记录。

        @param session: 异步数据库会话
        @param paths_by_from: {from_upgrade_ship_id: {sku_id: cost_cents}}
        @param updated_at: 本次更新 UTC 时间
        @return: 写入的路线边总数量
        """
        if not paths_by_from:
            return 0
        from_ids = list(paths_by_from.keys())
        await session.execute(
            delete(UpgradePath).where(UpgradePath.from_upgrade_ship_id.in_(from_ids))
        )
        new_rows: list[UpgradePath] = [
            UpgradePath(
                from_upgrade_ship_id=from_id,
                sku_id=sku_id,
                cost_cents=cost_cents,
                updated_at=updated_at,
            )
            for from_id, sku_map in paths_by_from.items()
            for sku_id, cost_cents in sku_map.items()
        ]
        if new_rows:
            session.add_all(new_rows)
            await session.flush()
        return len(new_rows)

    async def get_latest_paths_updated_at(self, session: AsyncSession) -> datetime | None:
        """
        获取最近一次升级路线可达图更新时间戳。

        @param session: 异步数据库会话
        @return: 最新 updated_at 时间或 None
        """
        stmt = select(UpgradePath.updated_at).order_by(UpgradePath.updated_at.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_stale_upgrade_paths(
        self,
        session: AsyncSession,
        active_sku_ids: Sequence[int],
    ) -> None:
        """
        清理不再在售的 CCU SKU 对应的路线边记录（当存在活跃列表时可选调用）。

        @param session: 异步数据库会话
        @param active_sku_ids: 当前在售 SKU ID 序列
        """
        if not active_sku_ids:
            return
        await session.execute(
            delete(UpgradePath).where(UpgradePath.sku_id.not_in(list(active_sku_ids)))
        )
