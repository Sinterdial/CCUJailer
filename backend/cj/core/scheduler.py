"""
进程内轻量定时调度器模块。

周期性触发 RSI CCU 抓取任务、全量舰船目录与汉化同步任务，以及历史日志自动清理任务；
支持从动态配置读取最新运行间隔并实现停机时毫秒级唤醒退出。
"""

import asyncio
from collections.abc import Awaitable, Callable

from cj.core.logging import get_logger

logger = get_logger(__name__)

AsyncJob = Callable[[], Awaitable[None]]
InterruptibleJob = Callable[[Callable[[], bool]], Awaitable[None]]
IntervalProvider = Callable[[], Awaitable[int]]


class BackgroundScheduler:
    """
    基于 asyncio 的轻量后台定时任务调度器。

    @ivar _tasks: 受托管的后台协程任务列表
    @ivar _stop_event: 用于优雅通知所有循环退出的事件信号
    @ivar _scrape_lock: 互斥锁，避免手动触发抓取与定时抓取并发冲突
    """

    def __init__(self) -> None:
        """
        初始化调度器停止信号与任务容器。
        """
        self._tasks: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()
        self._scrape_lock = asyncio.Lock()

    @property
    def scrape_lock(self) -> asyncio.Lock:
        """
        返回抓取任务互斥锁，供 API 手动触发接口共享防重入。

        @return: 异步互斥锁实例
        """
        return self._scrape_lock

    def is_stopping(self) -> bool:
        """
        判断调度器是否已收到优雅停机信号。

        @return: 停止中返回 True，正常运行返回 False
        """
        return self._stop_event.is_set()

    def start(
        self,
        scrape_job: AsyncJob,
        scrape_interval_provider: IntervalProvider,
        catalog_and_i18n_job: AsyncJob,
        catalog_interval_provider: IntervalProvider,
        cleanup_job: AsyncJob,
        plan_map_job: InterruptibleJob | None = None,
    ) -> None:
        """
        启动各核心周期性后台任务。

        @param scrape_job: CCU 与商店抓取 Diff 任务协程
        @param scrape_interval_provider: 动态获取抓取间隔秒数的异步回调
        @param catalog_and_i18n_job: 舰船目录、Wiki/UEX/货舱富化与汉化词典同步任务协程
        @param catalog_interval_provider: 动态获取目录同步间隔秒数的异步回调
        @param cleanup_job: 过期日志与历史记录清理任务协程
        @param plan_map_job: 可选升级路线可达图后台刷新任务协程
        """
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(
                self._run_scrape_loop(scrape_job, scrape_interval_provider),
                name="scheduler-ccu-scrape",
            ),
            asyncio.create_task(
                self._run_catalog_loop(
                    catalog_and_i18n_job,
                    catalog_interval_provider,
                    cleanup_job,
                ),
                name="scheduler-catalog-i18n",
            ),
        ]
        if plan_map_job is not None:
            self._tasks.append(
                asyncio.create_task(
                    self._run_plan_map_loop(plan_map_job),
                    name="scheduler-upgrade-plan-map",
                )
            )
        logger.info("BackgroundScheduler started with %d periodic loop(s)", len(self._tasks))

    async def _run_catalog_loop(
        self,
        catalog_job: AsyncJob,
        interval_provider: IntervalProvider,
        cleanup_job: AsyncJob,
    ) -> None:
        """
        执行每日全量目录、第三方数据源富化、汉化同步及过期日志清理循环。

        外部 HTTP 拉取与富化不占用高频抓取互斥锁，避免长时间阻塞手动同步。

        @param catalog_job: 目录与富化同步任务
        @param interval_provider: 间隔秒数读取回调
        @param cleanup_job: 过期日志清理任务
        """
        # 延迟 10 秒让高频商店/CCU 与初始界面优先就绪后再拉取大型 Wiki/UEX 数据集
        if await self._wait_or_stop(10):
            return

        while not self._stop_event.is_set():
            try:
                await catalog_job()
                await cleanup_job()
            except Exception:
                logger.exception("Catalog/i18n/cleanup loop iteration failed")

            interval = 86400
            try:
                interval = max(300, await interval_provider())
            except Exception:
                logger.warning("Failed to read catalog sync interval; using fallback 86400s")

            await self._wait_or_stop(interval)

    async def _run_plan_map_loop(self, plan_map_job: InterruptibleJob) -> None:
        """
        周期性构建全船升级路线可达图（每 3 小时执行一轮，停机时随时优雅中断）。

        @param plan_map_job: 接收 is_stopping 判定回调的路线图采集任务
        """
        # 首次启动延迟 45 秒待主商店及 CCU 基础数据入库后运行
        if await self._wait_or_stop(45):
            return

        while not self._stop_event.is_set():
            try:
                await plan_map_job(self.is_stopping)
            except Exception:
                logger.exception("Upgrade plan map loop iteration failed")
            await self._wait_or_stop(3 * 3600)

    async def _run_scrape_loop(
        self,
        scrape_job: AsyncJob,
        interval_provider: IntervalProvider,
    ) -> None:
        """
        执行高频 CCU 上下架监控抓取循环（首次延迟 3 秒等待初始目录预热后立即开始）。

        @param scrape_job: CCU 抓取与事件发布任务
        @param interval_provider: 动态读取抓取间隔的回调
        """
        if await self._wait_or_stop(3):
            return

        while not self._stop_event.is_set():
            try:
                async with self._scrape_lock:
                    await scrape_job()
            except Exception:
                logger.exception("CCU scrape loop iteration failed")

            interval = 300
            try:
                interval = max(30, await interval_provider())
            except Exception:
                logger.warning("Failed to read scrape interval; using fallback 300s")

            await self._wait_or_stop(interval)

    async def _wait_or_stop(self, seconds: int) -> bool:
        """
        在指定时长内可中断地睡眠，一旦收到停机信号立即返回 True。

        @param seconds: 计划等待的秒数
        @return: 若因停机信号被提前唤醒返回 True，正常到期返回 False
        """
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=float(seconds))
            return True
        except TimeoutError:
            return False

    async def stop(self, timeout: float = 20.0) -> None:
        """
        通知所有周期循环停止并等待进行中的抓取或清理任务安全退出。

        @param timeout: 等待进行中任务完成的最大秒数
        """
        self._stop_event.set()
        if not self._tasks:
            return
        logger.info("Stopping BackgroundScheduler (%d loop(s))", len(self._tasks))
        done, pending = await asyncio.wait(self._tasks, timeout=timeout)
        if pending:
            logger.warning("Cancelling %d timed-out scheduler loop(s)", len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        logger.info("BackgroundScheduler stopped (%d completed cleanly)", len(done))
