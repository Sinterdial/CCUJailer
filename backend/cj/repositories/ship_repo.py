"""
舰船数据访问层（Repository）。

封装 ships 表的查询、批量插入更新与双语检索，隔离底层数据库方言差异。
"""

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import Ship


class ShipRepository:
    """
    舰船 ORM 数据仓库。
    """

    async def list_all(self, session: AsyncSession) -> list[Ship]:
        """
        查询全量舰船记录，按英文名升序排列。

        @param session: 调用方提供的异步数据库会话
        @return: 舰船实体列表
        """
        stmt = select(Ship).order_by(Ship.name_en.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_filtered(
        self,
        session: AsyncSession,
        query: str | None = None,
        always_available_only: bool = False,
    ) -> list[Ship]:
        """
        按双语关键词或常驻可买状态筛选舰船列表。

        @param session: 调用方提供的异步数据库会话
        @param query: 双语搜索关键字（匹配英文名、中文名或 search_index）
        @param always_available_only: 是否仅返回常驻可买底船
        @return: 符合条件的舰船实体列表
        """
        stmt = select(Ship)
        if always_available_only:
            stmt = stmt.where(Ship.always_available.is_(True))
        if query and query.strip():
            pattern = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Ship.search_index).like(pattern),
                    func.lower(Ship.name_en).like(pattern),
                    func.lower(Ship.name_zh).like(pattern),
                )
            )
        stmt = stmt.order_by(Ship.value_cents.asc(), Ship.name_en.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, session: AsyncSession, ship_id: int) -> Ship | None:
        """
        根据主键 ID 获取单艘舰船。

        @param session: 异步数据库会话
        @param ship_id: 舰船主键 ID
        @return: 匹配到的舰船实体或 None
        """
        return await session.get(Ship, ship_id)

    async def get_by_key(self, session: AsyncSession, key: str) -> Ship | None:
        """
        根据归一化英文键名查询舰船。

        @param session: 异步数据库会话
        @param key: 归一化舰船键名
        @return: 匹配到的舰船实体或 None
        """
        stmt = select(Ship).where(Ship.key == key)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_upgrade_id(
        self,
        session: AsyncSession,
        rsi_upgrade_id: int,
    ) -> Ship | None:
        """
        根据 RSI 升级工具船只 ID 查询舰船。

        @param session: 异步数据库会话
        @param rsi_upgrade_id: RSI 升级接口中的船只 ID
        @return: 匹配到的舰船实体或 None
        """
        stmt = select(Ship).where(Ship.rsi_upgrade_id == rsi_upgrade_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_upgrade_candidates(
        self,
        session: AsyncSession,
        max_value_cents: int,
    ) -> list[Ship]:
        """
        查询全部可用于计算最近起始船的候选舰船（价值大于 0 且严格小于目标价格）。

        @param session: 异步数据库会话
        @param max_value_cents: 目标 CCU 购买价格（美分）
        @return: 候选舰船实体列表，按价值降序排列
        """
        stmt = (
            select(Ship)
            .where(Ship.value_cents > 0, Ship.value_cents < max_value_cents)
            .order_by(Ship.value_cents.desc(), Ship.always_available.desc(), Ship.name_en.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, session: AsyncSession, ship: Ship) -> Ship:
        """
        将新建的舰船实体加入当前会话并刷新以获取自增主键。

        @param session: 异步数据库会话
        @param ship: 待保存的 Ship 实体
        @return: 已刷新主键的 Ship 实体
        """
        session.add(ship)
        await session.flush()
        return ship

    async def add_all(self, session: AsyncSession, ships: Sequence[Ship]) -> None:
        """
        批量写入新建舰船实体并 flush。

        @param session: 异步数据库会话
        @param ships: Ship 实体序列
        """
        if not ships:
            return
        session.add_all(list(ships))
        await session.flush()

    async def count_all(self, session: AsyncSession) -> tuple[int, int]:
        """
        统计全量舰船数与已完成中文翻译的舰船数量。

        @param session: 异步数据库会话
        @return: (总舰船数, 已含中文译名数)
        """
        total_stmt = select(func.count()).select_from(Ship)
        zh_stmt = select(func.count()).select_from(Ship).where(Ship.name_zh != "")
        total = int((await session.execute(total_stmt)).scalar_one())
        translated = int((await session.execute(zh_stmt)).scalar_one())
        return total, translated
