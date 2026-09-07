import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from collectors.base import CollectionError
from collectors.domclick.collector import DomclickBlocked, DomclickCollector, set_page
from collectors.domclick.parser import parse_page

URL = "https://ufa.domclick.ru/search?deal_type=sale&rooms=2&offset=0"
SEARCH = SimpleNamespace(name="Домклик", url=URL)


def page(ids=("100",), *, time="час назад"):
    cards = "".join(f'''<article><a href="/card/{identifier}">2-комн. квартира 64,5 м² 12/17 эт.</a>
        <p>Уфа, улица Ленина, 1</p><p>Описание квартиры</p><span>11 000 000 ₽ 170 542 ₽/м²</span>
        <img src="https://images.example/{identifier}.jpg"><time>{time}</time></article>''' for identifier in ids)
    return f"<html><body>{cards}</body></html>"


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    settings.DOMCLICK_STATE_DIR = tmp_path / "domclick"
    settings.DOMCLICK_PROXY_URL = "socks5://localhost:1080"
    settings.DOMCLICK_PERSISTENT_PROFILE = True
    settings.DOMCLICK_PAGE_DELAY_SECONDS = 0
    settings.DOMCLICK_FULL_SCAN_ENABLED = True
    settings.DOMCLICK_MAX_PAGES = 50


def test_parser_reads_domclick_card_and_ignores_relative_date_in_description():
    item = parse_page(page(), URL).listings[0]
    assert item.external_id == "100"
    assert item.url == "https://ufa.domclick.ru/card/100"
    assert (item.rooms, str(item.area), item.floor, item.floors_total) == (2, "64.5", 12, 17)
    assert (item.price, item.price_per_sqm) == (11_000_000, 170_542)
    assert item.address == "Уфа, улица Ленина, 1"
    assert item.image_url == "https://images.example/100.jpg"
    assert item.published_text == "час назад"
    assert "час назад" not in item.description


def test_page_preserves_repeated_filters_for_technical_search():
    assert set_page("https://ufa.domclick.ru/search?rooms=2&rooms=3&offset=40", 2) == \
        "https://ufa.domclick.ru/search?rooms=2&rooms=3&offset=20"


def test_page_uses_public_listing_pagination_and_keeps_first_page_canonical():
    url = "https://ufa.domclick.ru/pokupka/kvartiry/odnokomnatnaja?sort=price"
    assert set_page(url, 1) == url
    assert set_page(url, 2) == url + "&page=2"


def test_collector_fast_and_full_completion():
    collector = DomclickCollector()
    collector._start = AsyncMock()
    collector._load_page = AsyncMock(side_effect=[page(), page(())])
    result = asyncio.run(collector.collect(SEARCH, mode="full"))
    assert result.complete and result.pages_scanned == 2 and len(result.listings) == 1
    assert "offset=20" in collector._load_page.call_args_list[1].args[0]


def test_damaged_page_fails_closed_and_preserves_items():
    collector = DomclickCollector()
    collector._start = AsyncMock()
    collector._load_page = AsyncMock(side_effect=[page(), '<a href="/card/broken">bad</a>'])
    result = asyncio.run(collector.collect(SEARCH, mode="full"))
    assert not result.complete and len(result.listings) == 1


def test_invalid_url_is_not_requested():
    collector = DomclickCollector()
    collector._start = AsyncMock()
    with pytest.raises(CollectionError, match="Domclick search or public listing"):
        asyncio.run(collector.collect(SimpleNamespace(url="https://evil.test/search"), mode="fast"))
    collector._start.assert_not_awaited()


def test_public_listing_url_is_accepted():
    collector = DomclickCollector()
    collector._start = AsyncMock()
    collector._load_page = AsyncMock(return_value=page(()))
    search = SimpleNamespace(url="https://ufa.domclick.ru/pokupka/kvartiry/odnokomnatnaja")
    result = asyncio.run(collector.collect(search, mode="fast"))
    assert result.complete
    collector._load_page.assert_awaited_once_with(search.url)


def test_proxy_is_required_before_browser(settings):
    settings.DOMCLICK_PROXY_URL = ""
    collector = DomclickCollector()
    collector._start = AsyncMock()
    with pytest.raises(CollectionError, match="direct fallback"):
        asyncio.run(collector.collect(SEARCH, mode="fast"))
    collector._start.assert_not_awaited()


def test_fresh_profile_cleanup_preserves_request_state(settings):
    settings.DOMCLICK_PERSISTENT_PROFILE = False
    driver = AsyncMock()
    start = AsyncMock(return_value=driver)

    async def check():
        async with DomclickCollector() as collector:
            collector.state.update(blocked_until=123, next_request_at=456)
            await collector._start()
            profile = Path(driver.chromium.launch_persistent_context.call_args.args[0])
            assert profile.is_dir()
            assert profile != settings.DOMCLICK_STATE_DIR / "chromium"
        assert not profile.exists()
        assert collector.state.read() == {"blocked_until": 123, "next_request_at": 456}

    with patch("collectors.domclick.collector.async_playwright", return_value=SimpleNamespace(start=start)):
        asyncio.run(check())


def test_fresh_profile_does_not_bypass_cooldown(settings):
    settings.DOMCLICK_PERSISTENT_PROFILE = False
    collector = DomclickCollector()
    collector.state.update(blocked_until=time.time() + 3600)
    with patch("collectors.domclick.collector.async_playwright") as driver:
        with pytest.raises(CollectionError, match="cooldown"):
            asyncio.run(collector.collect(SEARCH, mode="fast"))
        driver.assert_not_called()
    assert collector.temporary_profile is None


def test_manual_recovery_reads_open_page_without_second_navigation():
    collector = DomclickCollector(headless=False, manual_wait_seconds=10)
    collector.page = AsyncMock()
    collector.page.goto.return_value = SimpleNamespace(status=401, all_headers=AsyncMock(return_value={}))
    collector.page.content.side_effect = ["<title>Доступ ограничен</title>", page()]
    collector.page.url = URL
    collector.page.wait_for_function = AsyncMock()
    assert asyncio.run(collector._load_page(URL)) == page()
    collector.page.goto.assert_awaited_once()
    assert collector.state.read()["blocked_until"] == 0
    assert not collector.stop_requested


def test_manual_timeout_keeps_block_and_sends_no_retries():
    collector = DomclickCollector(headless=False, manual_wait_seconds=1)
    collector.page = AsyncMock()
    collector.page.goto.return_value = SimpleNamespace(status=401, all_headers=AsyncMock(return_value={}))
    collector.page.content.return_value = "<title>Доступ ограничен</title>"
    collector.page.url = URL
    with patch("collectors.domclick.collector.time", SimpleNamespace(time=time.time, monotonic=Mock(side_effect=[0, 2]))):
        with pytest.raises(DomclickBlocked, match="manual wait timeout"):
            asyncio.run(collector._load_page(URL))
    collector.page.goto.assert_awaited_once()
