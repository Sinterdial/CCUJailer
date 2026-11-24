"""
统一日志配置与脱敏过滤器模块。

所有生产代码必须通过本模块的 get_logger 获取 Logger，严禁使用 print()。
日志格式遵循 `[YYYY-MM-DD HH:MM:SS] [LEVEL] [module_name] message`，并默认输出至 stdout。
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cj.core.security import sanitize_log_text

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_LOG_COUNT = 5


class SecretRedactingFilter(logging.Filter):
    """
    日志脱敏过滤器。

    在日志记录写入控制台或文件前自动清理 URL 密码、Bearer Token 及运行时配置的密钥。

    @ivar _secrets: 需要屏蔽的明文密钥元组
    """

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        """
        初始化日志脱敏过滤器。

        @param secrets: 需要屏蔽的明文密钥元组
        """
        super().__init__()
        self._secrets = tuple(s for s in secrets if s and len(s) >= 4)

    def filter(self, record: logging.LogRecord) -> bool:
        """
        对单条日志消息进行格式化与脱敏处理。

        @param record: Python 标准库日志记录对象
        @return: 始终返回 True 以保留该条日志
        """
        rendered = record.getMessage()
        sanitized = sanitize_log_text(rendered, self._secrets)
        if sanitized != rendered:
            record.msg = sanitized
            record.args = ()
        return True


def configure_logging(
    level: str = "INFO",
    log_to_file: bool = False,
    data_dir: Path | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    """
    初始化全局根日志器与控制台/轮转文件处理器。

    @param level: 日志等级名称（DEBUG/INFO/WARNING/ERROR/CRITICAL）
    @param log_to_file: 是否同时写入 ./data/logs/ccujailer.log 轮转文件
    @param data_dir: 本地数据目录路径
    @param secrets: 需要在日志中屏蔽的明文密钥集合
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 1. 清理既有 Handler 避免重复挂载导致日志重复打印
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    redacting_filter = SecretRedactingFilter(secrets=secrets)

    # 2. 挂载 Docker 友好的 stdout 输出处理器
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redacting_filter)
    root_logger.addHandler(stream_handler)

    # 3. 按需挂载本地 10MB x 5 份的轮转文件处理器
    if log_to_file:
        base_dir = data_dir or Path("./data")
        log_dir = base_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "ccujailer.log",
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_LOG_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting_filter)
        root_logger.addHandler(file_handler)

    # 4. 抑制第三方底层通信库的冗余 DEBUG/INFO 噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(module_name: str) -> logging.Logger:
    """
    获取按模块名称命名的统一 Logger 实例。

    @param module_name: 调用模块的 __name__
    @return: 标准 Logger 实例
    """
    return logging.getLogger(module_name)
