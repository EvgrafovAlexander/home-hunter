import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from collectors.avito.collector import AvitoCollector, set_page
from collectors.base import CollectionError

HTML = (Path(__file__).parent / "fixtures/search.html").read_text().split('<div data-marker="item" data-item-id="broken">')[0]
SEARCH = SimpleNamespace(name="Test", url="https://www.avito.ru/ufa/kvartiry?s=104")


def collector_for(pages):
    collector = AvitoCollector()
    collector._start = AsyncMock()
    collector._load_page = AsyncMock(side_effect=pages)
    return collector


def test_pagination_url():
    assert set_page("https://www.avito.ru/ufa?s=104&p=9&f=a&f=b", 2) == "https://www.avito.ru/ufa?s=104&f=a&f=b&p=2"


def test_full_dedup_and_empty(settings):
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    collector = collector_for([HTML, HTML, ""])
    result = asyncio.run(collector.collect(SEARCH, mode="full"))
    assert result.complete and result.pages_scanned == 3
    assert result.items_seen == 4 and len(result.listings) == 2


def test_repeated_pages_are_partial(settings):
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    collector = collector_for([HTML, HTML, HTML])
    result = asyncio.run(collector.collect(SEARCH, mode="full"))
    assert not result.complete and result.pages_scanned == 3


def test_max_pages_partial(settings):
    settings.AVITO_MAX_PAGES = 1
    result = asyncio.run(collector_for([HTML]).collect(SEARCH, mode="full"))
    assert not result.complete


def test_fast_only_first_page():
    collector = collector_for([HTML])
    result = asyncio.run(collector.collect(SEARCH, mode="fast"))
    assert result.complete and collector._load_page.await_count == 1


def test_page_failure_preserves_progress(settings):
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    with pytest.raises(CollectionError) as caught:
        asyncio.run(collector_for([HTML, RuntimeError("blocked")]).collect(SEARCH, mode="full"))
    assert caught.value.result.pages_scanned == 1
    assert len(caught.value.result.listings) == 2


def test_damaged_card_prevents_deactivation(settings):
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    html = (Path(__file__).parent / "fixtures/search.html").read_text()
    result = asyncio.run(collector_for([html, ""]).collect(SEARCH, mode="full"))
    assert not result.complete and len(result.listings) == 2


@pytest.mark.parametrize("status,html,error", [
    (403, "", "HTTP error"),
    (200, "<body>Доступ ограничен</body>", "access blocked"),
    (200, "<body>Проверка безопасности</body>", "access blocked"),
    (200, "<body>Проблема с IP</body>", "access blocked"),
    (200, "<body>Unexpected layout</body>", "Unrecognized"),
])
def test_page_errors_fail(status, html, error):
    collector = AvitoCollector()
    collector.page = AsyncMock()
    collector.page.goto.return_value = SimpleNamespace(status=status, all_headers=AsyncMock(return_value={}))
    collector.page.content.return_value = html
    with pytest.raises(RuntimeError, match=error):
        asyncio.run(collector._load_page(SEARCH.url))


def test_recognized_empty_page():
    collector = AvitoCollector()
    collector.page = AsyncMock()
    collector.page.goto.return_value = SimpleNamespace(status=200, all_headers=AsyncMock(return_value={}))
    collector.page.content.return_value = '<div data-marker="search-results/empty">Ничего не найдено</div>'
    assert asyncio.run(collector._load_page(SEARCH.url))


def test_browser_starts_once_and_closes():
    driver = AsyncMock()
    context = driver.chromium.launch_persistent_context.return_value
    start = AsyncMock(return_value=driver)
    from unittest.mock import patch
    async def check():
        async with AvitoCollector() as collector:
            await collector._start()
            await collector._start()
    with patch("collectors.avito.collector.async_playwright", return_value=SimpleNamespace(start=start)):
        asyncio.run(check())
    start.assert_awaited_once()
    driver.chromium.launch_persistent_context.assert_awaited_once()
    context.close.assert_awaited_once()
    driver.stop.assert_awaited_once()


@pytest.mark.parametrize('proxy', ['', 'socks5://cian-tunnel:1080'])
def test_browser_uses_configured_proxy(settings, proxy):
    from unittest.mock import patch
    settings.AVITO_PROXY_URL = proxy
    driver = AsyncMock()
    start = AsyncMock(return_value=driver)
    async def check():
        async with AvitoCollector(headless=False) as collector:
            await collector._start()
    with patch('collectors.avito.collector.async_playwright', return_value=SimpleNamespace(start=start)):
        asyncio.run(check())
    kwargs = driver.chromium.launch_persistent_context.call_args.kwargs
    assert kwargs['headless'] is False
    if proxy:
        assert kwargs['proxy'] == {'server': proxy}
    else:
        assert 'proxy' not in kwargs


@pytest.mark.parametrize('proxy', ['localhost:1080', 'socks5://host', 'socks5://user:secret@host:1080',
                                  'http://host:1080/path', 'http://host:99999'])
def test_invalid_proxy_does_not_start_browser(settings, proxy):
    from unittest.mock import patch
    settings.AVITO_PROXY_URL = proxy
    with patch('collectors.avito.collector.async_playwright') as driver:
        collector = AvitoCollector()
        with pytest.raises(ValueError):
            asyncio.run(collector._start())
        assert collector.stop_requested
        driver.assert_not_called()


def test_proxy_failure_stops_without_retry(settings):
    from playwright.async_api import Error
    settings.AVITO_PROXY_URL = 'socks5://cian-tunnel:1080'
    collector = AvitoCollector()
    collector._start = AsyncMock()
    collector.page = AsyncMock()
    collector.page.goto.side_effect = Error('net::ERR_PROXY_CONNECTION_FAILED')
    with pytest.raises(CollectionError, match='ERR_PROXY_CONNECTION_FAILED'):
        asyncio.run(collector.collect(SEARCH, mode='fast'))
    assert collector.stop_requested
    collector.page.goto.assert_awaited_once()


def test_fresh_profile_cleanup_preserves_request_state(settings):
    from unittest.mock import patch
    settings.AVITO_PERSISTENT_PROFILE = False
    driver = AsyncMock()
    start = AsyncMock(return_value=driver)
    async def check():
        async with AvitoCollector() as collector:
            collector.state.update(blocked_until=123, next_request_at=456)
            await collector._start()
            profile = Path(driver.chromium.launch_persistent_context.call_args.args[0])
            assert profile.is_dir()
            assert profile != settings.AVITO_STATE_DIR / 'chromium'
        assert not profile.exists()
        assert collector.state.read() == {'blocked_until': 123, 'next_request_at': 456}
    with patch('collectors.avito.collector.async_playwright', return_value=SimpleNamespace(start=start)):
        asyncio.run(check())


def test_fresh_profile_does_not_bypass_cooldown(settings):
    import time
    from unittest.mock import patch
    settings.AVITO_PERSISTENT_PROFILE = False
    collector = AvitoCollector()
    collector.state.update(blocked_until=time.time() + 3600)
    with patch('collectors.avito.collector.async_playwright') as driver:
        with pytest.raises(CollectionError, match='cooldown'):
            asyncio.run(collector.collect(SEARCH, mode='fast'))
        driver.assert_not_called()
    assert collector.temporary_profile is None
