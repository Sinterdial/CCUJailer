"""
Webhook 规则与推送日志数据访问层。
"""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cj.db.models import WebhookLog, WebhookRule


class WebhookRepository:
    """
    Webhook 规则与日志 ORM 数据仓库。
    """

    async def list_rules(
        self, session: AsyncSession, enabled_only: bool = False
    ) -> list[WebhookRule]:
        """
        查询 Webhook 规则列表，按创建时间升序排列。

        @param session: 异步数据库会话
        @param enabled_only: 若为 True 仅返回已启用的规则
        @return: WebhookRule 实体列表
        """
        stmt = select(WebhookRule)
        if enabled_only:
            stmt = stmt.where(WebhookRule.enabled.is_(True))
        stmt = stmt.order_by(WebhookRule.created_at.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_rule(self, session: AsyncSession, rule_id: str) -> WebhookRule | None:
        """
        根据规则 ID 获取单个 Webhook 规则。

        @param session: 异步数据库会话
        @param rule_id: 规则唯一标识
        @return: 匹配的 WebhookRule 实体或 None
        """
        return await session.get(WebhookRule, rule_id)

    async def add_rule(self, session: AsyncSession, rule: WebhookRule) -> WebhookRule:
        """
        写入新的 Webhook 规则并 flush。

        @param session: 异步数据库会话
        @param rule: WebhookRule 实体
        @return: 已加入会话的 WebhookRule 实体
        """
        session.add(rule)
        await session.flush()
        return rule

    async def delete_rule(self, session: AsyncSession, rule: WebhookRule) -> None:
        """
        从数据库删除指定的 Webhook 规则（级联删除关联日志）。

        @param session: 异步数据库会话
        @param rule: 待删除的 WebhookRule 实体
        """
        await session.delete(rule)
        await session.flush()

    async def add_log(self, session: AsyncSession, log_entry: WebhookLog) -> WebhookLog:
        """
        写入单次 Webhook 推送记录并 flush。

        @param session: 异步数据库会话
        @param log_entry: WebhookLog 实体
        @return: 已刷新主键的 WebhookLog 实体
        """
        session.add(log_entry)
        await session.flush()
        return log_entry

    async def list_logs(
        self,
        session: AsyncSession,
        limit: int = 50,
        offset: int = 0,
        rule_id: str | None = None,
        success: bool | None = None,
    ) -> tuple[list[WebhookLog], int]:
        """
        分页查询近期 Webhook 推送执行日志。

        @param session: 异步数据库会话
        @param limit: 单页条数
        @param offset: 偏移量
        @param rule_id: 可选的规则 ID 过滤
        @param success: 可选的推送成功状态过滤
        @return: (推送日志列表, 总匹配条数)
        """
        base = select(WebhookLog)
        count_stmt = select(func.count()).select_from(WebhookLog)

        if rule_id and rule_id.strip():
            rid = rule_id.strip()
            base = base.where(WebhookLog.rule_id == rid)
            count_stmt = count_stmt.where(WebhookLog.rule_id == rid)
        if success is not None:
            base = base.where(WebhookLog.success.is_(success))
            count_stmt = count_stmt.where(WebhookLog.success.is_(success))

        stmt = (
            base.order_by(WebhookLog.created_at.desc(), WebhookLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        items = list((await session.execute(stmt)).scalars().all())
        total = int((await session.execute(count_stmt)).scalar_one())
        return items, total

    async def delete_logs_older_than(self, session: AsyncSession, cutoff: datetime) -> int:
        """
        清理早于截止时间的 Webhook 推送历史日志。

        @param session: 异步数据库会话
        @param cutoff: UTC 截止时间
        @return: 实际删除的记录条数
        """
        stmt = delete(WebhookLog).where(WebhookLog.created_at < cutoff)
        result = await session.execute(stmt)
        return int(result.rowcount or 0)
