"""
进程内异步事件总线模块。

用于在数据库事务成功提交后向订阅者（如 Webhook 通知引擎）分发领域事件，实现爬虫与通知渠道彻底解耦。
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from cj.core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class EventType(StrEnum):
    """
    系统领域事件类型枚举。
    """

    CCU_LISTED = "ccu.listed"
    CCU_PRICE_CHANGED = "ccu.price_changed"
    CCU_DELISTED = "ccu.delisted"
    STORE_LISTED = "store.listed"
    STORE_PRICE_CHANGED = "store.price_changed"
    I18N_UPDATED = "i18n.updated"


class EventBus:
    """
    基于 asyncio 的进程内异步事件总线。

    追踪所有正在执行的订阅协程，以便在应用优雅停机时等待异步通知任务完整落盘。

    @ivar _subscribers: 事件类型到处理函数列表的映射
    @ivar _in_flight: 当前正在后台运行的消费任务集合
    """

    def __init__(self) -> None:
        """
        初始化空的事件订阅表与在途任务跟踪集合。
        """
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._in_flight: set[asyncio.Task[None]] = set()

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """
        为指定领域事件注册异步消费处理器。

        @param event_type: 目标领域事件类型
        @param handler: 接收事件负载字典的异步处理函数
        """
        self._subscribers[event_type].append(handler)
        logger.debug(
            "Registered subscriber for event=%s handler=%s", event_type, handler.__qualname__
        )

    async def publish(self, event_type: EventType, payload: dict[str, Any]) -> None:
        """
        非阻塞派发领域事件，为每个订阅者创建独立后台任务执行。

        @param event_type: 领域事件类型
        @param payload: 已脱敏或结构化的事件负载字典
        """
        handlers = self._subscribers.get(event_type, [])
        if not handlers:
            return

        for handler in handlers:
            task = asyncio.create_task(
                self._run_handler(event_type, handler, payload),
                name=f"event-{event_type.value}",
            )
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)

    async def _run_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        payload: dict[str, Any],
    ) -> None:
        """
        受保护地执行单个事件处理器，捕获并记录异常以避免影响主调度流程。

        @param event_type: 事件类型
        @param handler: 事件处理器协程函数
        @param payload: 事件负载数据
        """
        try:
            await handler(payload)
        except Exception:
            logger.exception(
                "Unhandled exception in event subscriber event=%s handler=%s",
                event_type,
                handler.__qualname__,
            )

    async def wait_all(self, timeout: float = 15.0) -> None:
        """
        等待当前所有在途事件消费任务完成（用于 FastAPI 优雅停机阶段）。

        @param timeout: 最大等待秒数，超时后强制返回并打印告警
        """
        if not self._in_flight:
            return
        pending = list(self._in_flight)
        logger.info("Waiting for %d in-flight event task(s) to finish", len(pending))
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        if still_pending:
            logger.warning(
                "Timed out waiting for %d event task(s); cancelling remaining tasks",
                len(still_pending),
            )
            for task in still_pending:
                task.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)
        logger.info("Event bus drained (%d completed)", len(done))
