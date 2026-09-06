"""Opt-in real Chromium check against a local HTML fixture, never live Avito."""
import asyncio
import os
from pathlib import Path
import pytest
from collectors.avito.collector import AvitoCollector
from collectors.avito.parser import parse_page

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1")


def test_real_chromium_fixture_and_lifecycle():
    async def check():
        async with AvitoCollector() as collector:
            await collector._start()
            first_browser = collector.browser
            await collector._start()
            assert collector.browser is first_browser
            await collector.page.goto((Path(__file__).parent / "fixtures/search.html").resolve().as_uri())
            result = parse_page(await collector.page.content())
            assert len(result.listings) == 2
            assert result.listings[0].rooms == 2
        assert not first_browser.is_connected()
    asyncio.run(check())


def test_real_browser_captcha_recovery_without_reload():
    import json
    from .test_collector import HTML, SEARCH

    async def check():
        collector = AvitoCollector(headless=False, manual_wait_seconds=10)
        # Exercise manual recovery in a headless test browser; production CLI requires --headed.
        collector.headless = True
        requests = []
        async with collector:
            await collector._start()

            async def serve(route):
                if not route.request.is_navigation_request():
                    await route.abort()
                    return
                requests.append(route.request.url)
                body = "<html><body>Доступ ограничен<script>setTimeout(() => {document.body.innerHTML = " + json.dumps(HTML) + ";}, 300);</script></body></html>"
                await route.fulfill(status=429, content_type="text/html; charset=utf-8", body=body)

            await collector.context.route("**/*", serve)
            html = await collector._load_page(SEARCH.url)
            assert len(parse_page(html).listings) == 2
            assert requests == [SEARCH.url]
            assert not collector.stop_requested
    asyncio.run(check())


def test_real_browser_profile_keeps_cookies_between_runs():
    import time

    async def check():
        async with AvitoCollector() as first:
            await first._start()
            await first.context.add_cookies([{
                "name": "fixture_session", "value": "preserved", "domain": "example.test",
                "path": "/", "expires": time.time() + 3600,
            }])
        async with AvitoCollector() as second:
            await second._start()
            cookies = await second.context.cookies("https://example.test/")
            assert any(c["name"] == "fixture_session" and c["value"] == "preserved" for c in cookies)
    asyncio.run(check())
