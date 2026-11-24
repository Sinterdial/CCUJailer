"""
统一出站 HTTP 与代理客户端工厂模块。

所有外部 HTTP 请求（RSI 抓取、汉化词典同步、AI 翻译、Webhook 推送）必须通过本模块发起。
基于 Settings 显式构建路由 Transport，彻底规避宿主机残留环境变量或注册表代理干扰，
同时原生支持 NO_PROXY 中的域名、IP 与 CIDR 网段（如 192.168.5.0/24）匹配。
"""

import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from cj.core.config import Settings
from cj.core.logging import get_logger
from cj.core.security import mask_url_credentials

logger = get_logger(__name__)

DEFAULT_USER_AGENT = (
    "CCUJailer/0.1 (Star Citizen CCU Monitor; +https://github.com/Sinterdial/CCUJailer)"
)


def should_bypass_proxy(host: str, no_proxy: str) -> bool:
    """
    判断目标主机是否命中 NO_PROXY 绕过规则。

    支持 '*'、'localhost'、精确 IPv4/IPv6、CIDR 网段（如 192.168.5.0/24）
    以及域名/子域名后缀（如 181691.xyz 与 .181691.xyz）。

    @param host: 目标请求的主机名或 IP
    @param no_proxy: 逗号分隔的 NO_PROXY 规则字符串
    @return: 若应直连绕过代理返回 True，否则返回 False
    """
    clean_host = host.strip().strip("[]").lower()
    if not clean_host or not no_proxy.strip():
        return False

    host_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        host_ip = ipaddress.ip_address(clean_host)
    except ValueError:
        host_ip = None

    for raw_rule in no_proxy.split(","):
        rule = raw_rule.strip().lower()
        if not rule:
            continue
        if rule == "*":
            return True

        # 1. 处理 CIDR 网段匹配（如 192.168.5.0/24）
        if "/" in rule and host_ip is not None:
            try:
                network = ipaddress.ip_network(rule, strict=False)
                if host_ip in network:
                    return True
            except ValueError:
                pass
            continue

        # 2. 处理纯 IP 精确匹配
        clean_rule_ip = rule.strip("[]")
        if host_ip is not None:
            try:
                if host_ip == ipaddress.ip_address(clean_rule_ip):
                    return True
            except ValueError:
                pass

        # 3. 处理域名与子域名匹配（去掉可选前导点号与端口）
        domain = rule.split(":", 1)[0].lstrip(".")
        if not domain:
            continue
        if clean_host == domain or clean_host.endswith(f".{domain}"):
            return True

    return False


class RoutedAsyncTransport(httpx.AsyncBaseTransport):
    """
    按目标主机与协议显式分发直连或代理的异步 HTTP Transport。

    @ivar _no_proxy: 绕过代理的主机规则字符串
    @ivar _direct_transport: 直连底层传输实例
    @ivar _http_proxy_transport: HTTP 请求代理传输实例
    @ivar _https_proxy_transport: HTTPS 请求代理传输实例
    """

    def __init__(
        self,
        http_proxy: str | None = None,
        https_proxy: str | None = None,
        all_proxy: str | None = None,
        no_proxy: str = "",
    ) -> None:
        """
        根据显式代理配置初始化直连与代理传输池。

        @param http_proxy: HTTP 协议代理地址
        @param https_proxy: HTTPS 协议代理地址
        @param all_proxy: 兜底全局代理地址（支持 HTTP/HTTPS/SOCKS5）
        @param no_proxy: 绕过代理的规则列表
        """
        self._no_proxy = no_proxy
        self._direct_transport = httpx.AsyncHTTPTransport(trust_env=False)

        eff_http = http_proxy or all_proxy
        eff_https = https_proxy or all_proxy

        self._http_proxy_transport = (
            httpx.AsyncHTTPTransport(proxy=eff_http, trust_env=False) if eff_http else None
        )
        if eff_https and eff_https == eff_http:
            self._https_proxy_transport = self._http_proxy_transport
        elif eff_https:
            self._https_proxy_transport = httpx.AsyncHTTPTransport(
                proxy=eff_https, trust_env=False
            )
        else:
            self._https_proxy_transport = None

    def select_transport(self, url: httpx.URL) -> httpx.AsyncBaseTransport:
        """
        根据请求 URL 主机与协议选择实际传输实例。

        @param url: 目标请求 URL 对象
        @return: 应使用的底层 AsyncBaseTransport 实例
        """
        if should_bypass_proxy(url.host or "", self._no_proxy):
            return self._direct_transport
        if url.scheme == "https" and self._https_proxy_transport is not None:
            return self._https_proxy_transport
        if url.scheme == "http" and self._http_proxy_transport is not None:
            return self._http_proxy_transport
        return self._direct_transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """
        将异步 HTTP 请求路由至匹配到的直连或代理传输实例执行。

        @param request: httpx 请求对象
        @return: 底层传输返回的 httpx 响应对象
        """
        transport = self.select_transport(request.url)
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        """
        关闭所有持有的底层连接池，确保不重复释放共享传输对象。
        """
        seen: set[int] = set()
        for transport in (
            self._direct_transport,
            self._http_proxy_transport,
            self._https_proxy_transport,
        ):
            if transport is not None and id(transport) not in seen:
                seen.add(id(transport))
                await transport.aclose()


class NetworkManager:
    """
    出站 HTTP 客户端生命周期管理器。

    分别维护面向公网抓取的全局代理客户端与面向内部/第三方通知的 Webhook 专用客户端。

    @ivar _settings: 应用静态配置实例
    @ivar _proxy_client: 按配置路由代理的共享客户端
    @ivar _webhook_client: 受 WEBHOOK_USE_PROXY 控制的推送客户端
    """

    def __init__(self, settings: Settings) -> None:
        """
        初始化网络管理器。

        @param settings: 应用静态配置实例
        """
        self._settings = settings
        self._proxy_client: httpx.AsyncClient | None = None
        self._webhook_client: httpx.AsyncClient | None = None

    def _build_proxy_transport(self) -> RoutedAsyncTransport:
        """
        依据当前 Settings 构造带 NO_PROXY 路由能力的代理传输实例。

        @return: RoutedAsyncTransport 实例
        """
        return RoutedAsyncTransport(
            http_proxy=self._settings.http_proxy,
            https_proxy=self._settings.https_proxy,
            all_proxy=self._settings.all_proxy,
            no_proxy=self._settings.no_proxy,
        )

    async def startup(self) -> None:
        """
        创建并激活全局复用的异步 HTTP 客户端连接池。
        """
        self._proxy_client = httpx.AsyncClient(
            transport=self._build_proxy_transport(),
            trust_env=False,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

        webhook_transport: httpx.AsyncBaseTransport = (
            self._build_proxy_transport()
            if self._settings.webhook_use_proxy
            else httpx.AsyncHTTPTransport(trust_env=False)
        )
        self._webhook_client = httpx.AsyncClient(
            transport=webhook_transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, connect=8.0),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

        proxy_summary = (
            mask_url_credentials(
                self._settings.https_proxy
                or self._settings.http_proxy
                or self._settings.all_proxy
                or ""
            )
            or "direct"
        )
        logger.info(
            "NetworkManager started (proxy=%s, no_proxy='%s', webhook_use_proxy=%s)",
            proxy_summary,
            self._settings.no_proxy,
            self._settings.webhook_use_proxy,
        )

    async def shutdown(self) -> None:
        """
        优雅关闭全部底层 HTTP 连接池，释放套接字资源。
        """
        if self._proxy_client is not None:
            await self._proxy_client.aclose()
            self._proxy_client = None
        if self._webhook_client is not None:
            await self._webhook_client.aclose()
            self._webhook_client = None
        logger.info("NetworkManager closed all HTTP clients")

    @property
    def proxy_client(self) -> httpx.AsyncClient:
        """
        获取启用了配置路由代理的通用出站客户端。

        @return: 已初始化的 httpx.AsyncClient 实例
        @raise RuntimeError: 当客户端尚未调用 startup() 初始化时抛出
        """
        if self._proxy_client is None:
            raise RuntimeError("NetworkManager has not been started")
        return self._proxy_client

    @property
    def webhook_client(self) -> httpx.AsyncClient:
        """
        获取专用于发送 Webhook 请求的异步客户端。

        @return: 已初始化的 httpx.AsyncClient 实例
        @raise RuntimeError: 当客户端尚未调用 startup() 初始化时抛出
        """
        if self._webhook_client is None:
            raise RuntimeError("NetworkManager has not been started")
        return self._webhook_client

    @asynccontextmanager
    async def new_rsi_cookie_client(
        self, timeout_seconds: float = 30.0
    ) -> AsyncIterator[httpx.AsyncClient]:
        """
        创建拥有独立 Cookie 容器且复用相同显式代理路由的临时 RSI 会话客户端。

        每次抓取单船或升级商店时使用独立 CookieJar，防止并发任务间 Rsi-Token 串台。

        @param timeout_seconds: 请求超时上限（秒）
        @return: 异步上下文管理器，产出一个临时的 httpx.AsyncClient
        """
        async with httpx.AsyncClient(
            transport=self._build_proxy_transport(),
            trust_env=False,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            headers={"User-Agent": DEFAULT_USER_AGENT},
        ) as client:
            yield client
