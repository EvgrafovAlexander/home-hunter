import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from collectors.proxy_pool import parse_proxy_list, select_working_proxy
from collectors.routing import RouteSpec


def test_parse_proxy_list_keeps_high_anonymity_candidates_only():
    html = """
    <table>
      <tr><td>1.2.3.4:8080</td><td>HTTP HIA</td></tr>
      <tr><td>5.6.7.8:1080</td><td>SOCKS5 HIA</td></tr>
      <tr><td>9.9.9.9:3128</td><td>HTTP NOA</td></tr>
      <tr><td>1.2.3.4:8080</td><td>HTTP HIA</td></tr>
    </table>
    """
    assert [candidate.url for candidate in parse_proxy_list(html)] == [
        "http://1.2.3.4:8080", "socks5://5.6.7.8:1080",
    ]


def test_select_working_proxy_tries_a_bounded_number(monkeypatch):
    html = """
    <table>
      <tr><td>1.2.3.4:8080</td><td>HTTP HIA</td></tr>
      <tr><td>5.6.7.8:1080</td><td>SOCKS5 HIA</td></tr>
    </table>
    """
    async def fetch(*args, **kwargs):
        return html

    monkeypatch.setattr("collectors.proxy_pool.fetch_proxy_list", fetch)
    first_browser = AsyncMock()
    first_browser.new_page.return_value.goto.side_effect = RuntimeError("unavailable")
    second_browser = AsyncMock()
    second_browser.new_page.return_value.goto.return_value = SimpleNamespace(status=200)
    launch = AsyncMock(side_effect=[first_browser, second_browser])
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    route = RouteSpec(
        code="pool", name="Pool", kind="free_proxy_pool", proxy_url="",
        settings={"max_candidates": 2, "max_attempts": 2, "probe_timeout_seconds": 1},
    )

    result = asyncio.run(select_working_proxy(playwright, route))

    assert result == {"server": "socks5://5.6.7.8:1080"}
    assert launch.await_count == 2
    first_browser.close.assert_awaited_once()
    second_browser.close.assert_awaited_once()
