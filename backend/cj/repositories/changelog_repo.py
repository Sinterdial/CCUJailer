"""
CCU 历史变动日志（Changelog）数据访问层。
"""

from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import Changelog


class ChangelogRepository:
    """
    Changelog ORM 数据仓库。
    """

    async def add(self, session: AsyncSession, entry: Changelog) -> Changelog:
        """
        追加一条变动日志并 flush。

        @param session: 异步数据库会话
        @param entry: Changelog 实体
        @return: 已刷新主键的 Changelog 实体
        """
        session.add(entry)
        await session.flush()
        return entry

    async def list_recent(
        self,
        session: AsyncSession,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        query: str | None = None,
    ) -> tuple[list[Changelog], int]:
        """
        按时间倒序分页查询历史上下架与调价日志。

        @param session: 异步数据库会话
        @param limit: 单页返回条数
        @param offset: 偏移量
        @param event_type: 可选的事件类型过滤
        @param query: 可选的中英文船名搜索关键字
        @return: (记录列表, 符合条件的总记录数)
        """
        base = select(Changelog)
        count_stmt = select(func.count()).select_from(Changelog)

        if event_type and event_type.strip():
            ev = event_type.strip()
            if ev == "ccu.wb_delisted":
                cond_ev = (Changelog.event_type == "ccu.delisted") & (
                    Changelog.is_warbond.is_(True)
                )
                base = base.where(cond_ev)
                count_stmt = count_stmt.where(cond_ev)
            elif ev == "ccu.standard_delisted":
                cond_ev = (Changelog.event_type == "ccu.delisted") & (
                    Changelog.is_warbond.is_(False)
                )
                base = base.where(cond_ev)
                count_stmt = count_stmt.where(cond_ev)
            else:
                base = base.where(Changelog.event_type == ev)
                count_stmt = count_stmt.where(Changelog.event_type == ev)

        if query and query.strip():
            pattern = f"%{query.strip().lower()}%"
            cond = or_(
                func.lower(Changelog.ship_name_en).like(pattern),
                func.lower(Changelog.ship_name_zh).like(pattern),
                func.lower(Changelog.edition_label).like(pattern),
            )
            base = base.where(cond)
            count_stmt = count_stmt.where(cond)

        stmt = (
            base.order_by(Changelog.occurred_at.desc(), Changelog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await session.execute(stmt)).scalars().all())
        total = int((await session.execute(count_stmt)).scalar_one())
        return items, total

    async def delete_older_than(self, session: AsyncSession, cutoff: datetime) -> int:
        """
        清理早于指定截止时间的历史变动记录。

        @param session: 异步数据库会话
        @param cutoff: UTC 截止时间，早于该时间的记录将被删除
        @return: 实际删除的记录条数
        """
        stmt = delete(Changelog).where(Changelog.occurred_at < cutoff)
        result = await session.execute(stmt)
        return int(result.rowcount or 0)
