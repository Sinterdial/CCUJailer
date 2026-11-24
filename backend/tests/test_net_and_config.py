"""
配置优先级与显式代理路由传输单元测试模块。

验证 .env 配置覆盖宿主机残留代理环境变量、缺少协议头自动补全、
NO_PROXY 对 CIDR 网段与子域名的准确匹配，以及 NetworkManager 路由隔离。
"""

from pathlib import Path

import httpx
import pytest

from cj.core.config import Settings
from cj.core.net import NetworkManager, RoutedAsyncTransport, should_bypass_proxy


def test_dotenv_overrides_stale_os_proxy_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证当宿主机存在旧代理环境变量时，项目 .env 中的代理配置优先胜出。

    @param tmp_path: Pytest 临时路径夹具
    @param monkeypatch: Pytest 环境变量替换夹具
    """
    monkeypatch.setenv("HTTP_PROXY", "http://192.168.5.25:15574")
    monkeypatch.setenv("HTTPS_PROXY", "http://192.168.5.25:15574")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "HTTP_PROXY=192.168.5.1:15574\n"
        "HTTPS_PROXY=http://192.168.5.1:15574\n"
        "ALL_PROXY=\n"
        "NO_PROXY=localhost,127.0.0.1,181691.xyz,192.168.5.0/24\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_path)
    assert loaded.http_proxy == "http://192.168.5.1:15574"
    assert loaded.https_proxy == "http://192.168.5.1:15574"
    assert loaded.all_proxy is None
    assert "192.168.5.0/24" in loaded.no_proxy


def test_should_bypass_proxy_supports_cidr_and_domains() -> None:
    """
    验证 NO_PROXY 规则正确匹配 CIDR 网段、本地地址与子域名且不误伤外部地址。
    """
    no_proxy = "localhost,127.0.0.1,181691.xyz,.corp.local,192.168.5.0/24"

    assert should_bypass_proxy("localhost", no_proxy) is True
    assert should_bypass_proxy("127.0.0.1", no_proxy) is True
    assert should_bypass_proxy("192.168.5.88", no_proxy) is True
    assert should_bypass_proxy("192.168.6.88", no_proxy) is False
    assert should_bypass_proxy("181691.xyz", no_proxy) is True
    assert should_bypass_proxy("bot.181691.xyz", no_proxy) is True
    assert should_bypass_proxy("evil181691.xyz", no_proxy) is False
    assert should_bypass_proxy("api.corp.local", no_proxy) is True
    assert should_bypass_proxy("robertsspaceindustries.com", no_proxy) is False


async def test_network_manager_explicit_proxy_and_webhook_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证 NetworkManager 按 Settings 构造显式路由传输，不受宿主环境变量干扰，
    且 WEBHOOK_USE_PROXY=False 时 Webhook 客户端始终直连。

    @param tmp_path: Pytest 临时路径夹具
    @param monkeypatch: Pytest 环境变量替换夹具
    """
    monkeypatch.setenv("HTTP_PROXY", "http://192.168.5.25:15574")
    monkeypatch.setenv("HTTPS_PROXY", "http://192.168.5.25:15574")

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        http_proxy="http://192.168.5.1:15574",
        https_proxy="http://192.168.5.1:15574",
        no_proxy="localhost,127.0.0.1,181691.xyz,192.168.5.0/24",
        webhook_use_proxy=False,
    )
    net = NetworkManager(settings)
    await net.startup()
    try:
        transport = net.proxy_client._transport
        assert isinstance(transport, RoutedAsyncTransport)

        rsi_transport = transport.select_transport(
            httpx.URL("https://robertsspaceindustries.com/graphql")
        )
        local_transport = transport.select_transport(httpx.URL("http://192.168.5.10:8080/hook"))
        domain_bypass = transport.select_transport(httpx.URL("https://push.181691.xyz/notify"))

        assert rsi_transport is transport._https_proxy_transport
        assert local_transport is transport._direct_transport
        assert domain_bypass is transport._direct_transport

        # WEBHOOK_USE_PROXY=False 时应为纯直连 AsyncHTTPTransport，而非代理路由
        assert not isinstance(net.webhook_client._transport, RoutedAsyncTransport)
    finally:
        await net.shutdown()
