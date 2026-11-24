"""
CCU SKU 数据访问层（Repository）。

封装 ccu_skus 表的读取、状态筛选与快照持久化写入。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import CcuSku, Ship


class CcuRepository:
    """
    CCU SKU ORM 数据仓库。
    """

    async def list_all(self, session: AsyncSession) -> list[CcuSku]:
        """
        获取全部 CCU SKU 记录（包含在售与已下架）。

        @param session: 异步数据库会话
        @return: CcuSku 实体列表
        """
        stmt = select(CcuSku).order_by(CcuSku.sku_id.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_listed_with_ships(
        self,
        session: AsyncSession,
        query: str | None = None,
        warbond_only: bool = False,
        min_price_cents: int | None = None,
        max_price_cents: int | None = None,
        min_savings_cents: int | None = None,
    ) -> list[tuple[CcuSku, Ship]]:
        """
        联合查询当前所有在售 CCU SKU 及其对应的目标舰船信息。

        @param session: 异步数据库会话
        @param query: 双语关键词（匹配舰船英文/中文名、搜索索引或版本标题）
        @param warbond_only: 是否仅返回 Warbond 优惠版本
        @param min_price_cents: 最低现价（美分）
        @param max_price_cents: 最高现价（美分）
        @param min_savings_cents: 最低优惠立省金额（美分）
        @return: (CcuSku, Ship) 元组列表，按现价升序及舰船英文名排列
        """
        stmt = (
            select(CcuSku, Ship)
            .join(Ship, CcuSku.ship_id == Ship.id)
            .where(CcuSku.listed.is_(True))
        )
        if warbond_only:
            stmt = stmt.where(CcuSku.is_warbond.is_(True))
        if min_price_cents is not None:
            stmt = stmt.where(CcuSku.price_cents >= min_price_cents)
        if max_price_cents is not None:
            stmt = stmt.where(CcuSku.price_cents <= max_price_cents)
        if min_savings_cents is not None:
            stmt = stmt.where(
                (CcuSku.original_price_cents - CcuSku.price_cents) >= min_savings_cents
            )
        if query and query.strip():
            pattern = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Ship.search_index).like(pattern),
                    func.lower(Ship.name_en).like(pattern),
                    func.lower(Ship.name_zh).like(pattern),
                    func.lower(CcuSku.title).like(pattern),
                )
            )

        stmt = stmt.order_by(
            CcuSku.is_warbond.desc(),
            CcuSku.price_cents.asc(),
            Ship.name_en.asc(),
        )
        result = await session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def count_listed(self, session: AsyncSession) -> tuple[int, int]:
        """
        统计当前在售 CCU 总数与其中的 Warbond CCU 数量。

        @param session: 异步数据库会话
        @return: (在售 CCU 总数, 在售 WBCCU 数量)
        """
        total_stmt = select(func.count()).select_from(CcuSku).where(CcuSku.listed.is_(True))
        wb_stmt = (
            select(func.count())
            .select_from(CcuSku)
            .where(CcuSku.listed.is_(True), CcuSku.is_warbond.is_(True))
        )
        total = int((await session.execute(total_stmt)).scalar_one())
        warbond = int((await session.execute(wb_stmt)).scalar_one())
        return total, warbond

    async def get_last_seen_at(self, session: AsyncSession):
        """
        查询最近一次成功刷新 CCU 快照的时间戳。

        @param session: 异步数据库会话
        @return: 最新 last_seen_at 时间或 None
        """
        stmt = select(func.max(CcuSku.last_seen_at))
        return (await session.execute(stmt)).scalar_one_or_none()

    async def add(self, session: AsyncSession, sku: CcuSku) -> CcuSku:
        """
        将新建的 CcuSku 实体加入会话并 flush。

        @param session: 异步数据库会话
        @param sku: 待写入的 CcuSku 实例
        @return: 已加入会话的 CcuSku 实例
        """
        session.add(sku)
        await session.flush()
        return sku
