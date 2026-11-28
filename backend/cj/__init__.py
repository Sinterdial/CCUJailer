"""
CCUJailer（CCU监狱）后端主包。

提供基于轻量模块化单体架构的 RSI CCU 抓取、双语映射、双引擎持久化及 Webhook 通知服务。
"""

from importlib.metadata import PackageNotFoundError, version

GITHUB_REPO_URL = "https://github.com/Sinterdial/CCUJailer"

try:
    APP_VERSION = version("ccujailer")
except PackageNotFoundError:
    APP_VERSION = "0.2.0"

