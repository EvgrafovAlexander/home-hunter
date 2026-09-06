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
