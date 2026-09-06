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
    collector.page.goto.return_value = SimpleNamespace(status=status)
    collector.page.content.return_value = html
    with pytest.raises(RuntimeError, match=error):
        asyncio.run(collector._load_page(SEARCH.url))


def test_recognized_empty_page():
    collector = AvitoCollector()
    collector.page = AsyncMock()
    collector.page.goto.return_value = SimpleNamespace(status=200)
    collector.page.content.return_value = '<div data-marker="search-results/empty">Ничего не найдено</div>'
    assert asyncio.run(collector._load_page(SEARCH.url))


def test_browser_starts_once_and_closes():
    driver = AsyncMock()
    browser = driver.chromium.launch.return_value
    start = AsyncMock(return_value=driver)
    from unittest.mock import patch
    async def check():
        async with AvitoCollector() as collector:
            await collector._start()
            await collector._start()
    with patch("collectors.avito.collector.async_playwright", return_value=SimpleNamespace(start=start)):
        asyncio.run(check())
    start.assert_awaited_once()
    driver.chromium.launch.assert_awaited_once_with(headless=True)
    browser.close.assert_awaited_once()
    driver.stop.assert_awaited_once()
