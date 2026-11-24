"""
动态系统配置数据访问层。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import SystemSetting, utc_now


class SettingRepository:
    """
    system_settings 表 ORM 数据仓库。
    """

    async def get_all_map(self, session: AsyncSession) -> dict[str, str]:
        """
        获取数据库中已保存的全部动态配置键值字典。

        @param session: 异步数据库会话
        @return: {配置键名: 配置文本值} 字典
        """
        stmt = select(SystemSetting)
        rows = (await session.execute(stmt)).scalars().all()
        return {row.key: row.value for row in rows}

    async def upsert_many(self, session: AsyncSession, items: dict[str, str]) -> None:
        """
        批量插入或更新动态配置项并 flush。

        @param session: 异步数据库会话
        @param items: 待写入的配置键值对字典
        """
        if not items:
            return
        now = utc_now()
        for key, value in items.items():
            existing = await session.get(SystemSetting, key)
            if existing is None:
                session.add(SystemSetting(key=key, value=value, updated_at=now))
            else:
                existing.value = value
                existing.updated_at = now
        await session.flush()
