import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from collectors.base import CollectionError
from collectors.domclick.collector import DomclickCollector, set_offset
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


def test_offset_preserves_repeated_filters():
    assert set_offset("https://ufa.domclick.ru/search?rooms=2&rooms=3&offset=40", 20) == \
        "https://ufa.domclick.ru/search?rooms=2&rooms=3&offset=20"


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
    with pytest.raises(CollectionError, match="Domclick /search"):
        asyncio.run(collector.collect(SimpleNamespace(url="https://evil.test/search"), mode="fast"))
    collector._start.assert_not_awaited()
