import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from collectors.base import CollectionError
from collectors.cian.collector import CianCollector, blocked, search_url
from collectors.cian.parser import parse_detail_page, parse_page

URL = 'https://ufa.cian.ru/cat.php?region=176245&room2=1&room3=1'
SEARCH = SimpleNamespace(pk=1, name='CIAN', url=URL)


def html(ids=(330738483,), *, page=1, next_page=None, total=None, newest=False, damaged=False, built_year=None):
    offers = []
    cards = []
    for identifier in ids:
        offers.append({'id':identifier, 'fullUrl':f'https://ufa.cian.ru/sale/flat/{identifier}/?context=volatile',
                       'title':None, 'formattedFullInfo':'2-комн.кв. · 73 м² · 8/25 этаж',
                       'totalArea':'73.0', 'roomsCount':2, 'floorNumber':8, 'building':{'floorsCount':25, 'buildYear':built_year},
                       'bargainTerms':{'priceRur':10000000}, 'geo':{'address':[{'fullName':'р-н Советский','name':'Советский','type':'raion'}]},
                       'description':'Публичное описание квартиры', 'added':'сегодня, 19:18',
                       'photos':[{'fullUrl':'https://images.cdn-cian.ru/images/example.jpg'}]})
        cards.append(f'<article data-name="CardComponent"><a data-name="TitleComponent" href="https://ufa.cian.ru/sale/flat/{identifier}/">2-комн. квартира</a><span data-mark="MainPrice">10 000 000 ₽</span><span data-mark="PriceInfo">136 986 ₽/м²</span></article>')
    if damaged:
        offers[0]['id'] = None
    results = {'offers':offers, 'totalOffers':len(ids) if total is None else total,
               'queryString':urlsplit(search_url(URL,page,newest=newest)).query}
    config = json.dumps([{'key':'initialState','value':{'results':results}}],ensure_ascii=False)
    next_link = f'<a href="{search_url(URL,next_page,newest=newest)}">Дальше</a>' if next_page else ''
    return '<html><title>ЦИАН</title><body>'+''.join(cards)+next_link+"<script>window._cianConfig['frontend-serp'] = (window._cianConfig['frontend-serp'] || []).concat("+config+");</script></body></html>"


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    settings.CIAN_STATE_DIR = tmp_path/'cian'
    settings.CIAN_PROXY_URL = 'socks5://localhost:1080'
    settings.CIAN_PAGE_DELAY_MIN_SECONDS = 0.01
    settings.CIAN_PAGE_DELAY_MAX_SECONDS = 0.01
    settings.CIAN_FULL_BATCH_PAGES = 50
    settings.CIAN_FULL_SCAN_ENABLED = True


def collector(pages):
    c = CianCollector()
    c._start = AsyncMock()
    c._load_page = AsyncMock(side_effect=pages)
    return c


def test_embedded_data_and_canonical_url():
    item = parse_page(html(),URL).listings[0]
    assert item.external_id == '330738483'
    assert item.url == 'https://ufa.cian.ru/sale/flat/330738483/'
    assert (item.area, item.floor, item.floors_total, item.price, item.price_per_sqm) == (Decimal('73'),8,25,10000000,136986)
    assert item.title and item.district == 'Советский'
    assert item.raw_data == {}


def test_extracts_cian_building_year():
    assert parse_page(html(built_year=2016), URL).listings[0].built_year == 2016


def test_parses_published_detail_and_photo_ids():
    state = {"offerData": {"pageHelmetData": {"title": "Квартира"}, "offer": {
        "id": 330738483, "status": "published", "priceTotalRur": 10000000,
        "totalArea": "73", "roomsCount": 2, "floorNumber": 8,
        "kitchenArea": "12.5", "livingArea": "42.1", "combinedWcsCount": 1,
        "balconiesCount": 1, "repairType": "euro", "hasFurniture": True,
        "building": {"floorsCount": 25, "buildYear": 2016, "materialType": "monolith", "ceilingHeight": "2.7"}, "geo": {"address": [], "coordinates": {"lat": 54.7355, "lng": 55.9586}},
        "photos": [{"id": 11, "isDefault": True, "fullUrl": "https://images.cdn-cian.ru/images/11.jpg"}],
        "editDate": "2026-09-10T08:45:00Z",
    }}}
    config = json.dumps([{"key": "defaultState", "value": state}])
    page = "<script>window._cianConfig['frontend-offer-card']=(window._cianConfig['frontend-offer-card']||[]).concat(" + config + ");</script>"
    detail = parse_detail_page(page, "https://ufa.cian.ru/sale/flat/330738483/")
    assert detail.status == "published" and detail.listing.price == 10000000 and detail.photo_ids == [11]
    assert detail.photos[0]["full_url"] == "https://images.cdn-cian.ru/images/11.jpg"
    assert detail.offer_data == state["offerData"]
    assert detail.latitude == Decimal("54.7355") and detail.longitude == Decimal("55.9586")
    assert detail.attributes["kitchen_area"] == Decimal("12.5")
    assert detail.attributes["bathrooms_combined"] == 1
    assert detail.attributes["building_material_type"] == "monolith"


def test_detail_without_marketing_title_does_not_make_technical_title():
    state = {"offerData": {"pageHelmetData": {"title": ""}, "offer": {
        "id": 330738483, "status": "published", "geo": {"address": []}, "photos": [],
    }}}
    config = json.dumps([{"key": "defaultState", "value": state}])
    page = "<script>window._cianConfig['frontend-offer-card']=(window._cianConfig['frontend-offer-card']||[]).concat(" + config + ");</script>"
    assert parse_detail_page(page, "https://ufa.cian.ru/sale/flat/330738483/").listing.title == ""


def test_detail_404_is_unavailable_not_a_batch_stopping_error():
    c = CianCollector()
    c.page = AsyncMock()
    c.page.url = "https://ufa.cian.ru/sale/flat/330738483/"
    c.page.goto.return_value = SimpleNamespace(status=404, all_headers=AsyncMock(return_value={}))
    c.page.content.return_value = "<html><title>Не найдено</title></html>"

    html = asyncio.run(c._load_page(
        "https://ufa.cian.ru/sale/flat/330738483/", expected_path="/sale/flat/330738483/",
    ))

    assert html is None
    assert not c.stop_requested


def test_damaged_card_and_false_empty():
    assert parse_page(html(damaged=True),URL).errors
    with pytest.raises(ValueError):
        parse_page('<html>Unknown layout</html>',URL)
    with pytest.raises(ValueError):
        parse_page(html(ids=(),total=641),URL)
    assert parse_page(html(ids=()),URL).total == 0


@pytest.mark.parametrize('url',['https://cian.ru.evil.test/cat.php','http://ufa.cian.ru/cat.php','https://evilcian.ru/cat.php','https://x@ufa.cian.ru/cat.php'])
def test_reject_foreign_urls(url):
    with pytest.raises(ValueError):
        search_url(url)


def test_fast_sorts_newest():
    c = collector([html(newest=True,total=641,next_page=2)])
    result = asyncio.run(c.collect(SEARCH,mode='fast'))
    assert result.complete and len(result.listings)==1
    assert 'sort=creation_date_desc' in c._load_page.call_args.args[0]


def test_full_follows_links_and_completes():
    c = collector([html(total=2,next_page=2),html((330738484,),page=2,total=2)])
    result = asyncio.run(c.collect(SEARCH,mode='full'))
    assert result.complete and result.pages_scanned==2 and len(result.listings)==2


def test_full_continues_when_live_total_changes():
    c = collector([
        html((1,), total=3, next_page=2),
        html((2,), page=2, total=4, next_page=3),
        html((3,), page=3, total=4, next_page=4),
        html((4,), page=4, total=4),
    ])
    result = asyncio.run(c.collect(SEARCH, mode='full'))
    assert result.complete and result.pages_scanned == 4
    assert result.expected_total == 4 and len(result.listings) == 4
    assert result.total_changes == 1


def test_retries_damaged_page_in_fresh_session():
    c = collector([html(damaged=True), html()])
    result = asyncio.run(c.collect(SEARCH, mode='full'))
    assert result.complete and len(result.listings) == 1
    assert result.safe_for_deactivation and not result.skipped_cards
    assert c._load_page.await_count == 2
    assert c._start.await_count == 2


def test_keeps_valid_cards_after_repeated_damage_without_failing_cycle():
    c = collector([html((1, 2), page=2, damaged=True), html((1, 2), page=2, damaged=True)])
    result = asyncio.run(c.collect(SEARCH, mode='full', start_page=2))
    assert result.complete and len(result.listings) == 1
    assert not result.safe_for_deactivation
    assert result.skipped_cards >= 1


def test_reports_progress_after_each_page():
    reported = []

    async def progress(*values):
        reported.append(values)

    c = collector([html(total=2,next_page=2),html((330738484,),page=2,total=2)])
    asyncio.run(c.collect(SEARCH, mode='full', progress_callback=progress))
    assert reported == [(1, 1, 1), (2, 2, 2)]


def test_partial_and_repeat_protect_deactivation(settings):
    result = asyncio.run(collector([html(total=641)]).collect(SEARCH,mode='full'))
    assert not result.complete
    settings.CIAN_FULL_BATCH_PAGES=1
    result = asyncio.run(collector([html(total=641,next_page=2)]).collect(SEARCH,mode='full'))
    assert not result.complete
    settings.CIAN_FULL_BATCH_PAGES=50
    c = collector([html(total=3,next_page=2),html(page=2,total=3,next_page=3)])
    with pytest.raises(CollectionError,match='repeated page') as error:
        asyncio.run(c.collect(SEARCH,mode='full'))
    assert len(error.value.result.listings)==1


def test_failure_preserves_collected_items():
    c=collector([html(total=2,next_page=2), RuntimeError('proxy unavailable')])
    with pytest.raises(CollectionError) as error:
        asyncio.run(c.collect(SEARCH,mode='full'))
    assert len(error.value.result.listings)==1


def test_query_mismatch_is_failure():
    with pytest.raises(CollectionError,match='query differs'):
        asyncio.run(collector([html(page=2)]).collect(SEARCH,mode='full'))


@pytest.mark.parametrize('status,body,final_url',[(403,'blocked',URL),(200,'<title>Вы не робот?</title>',URL),(200,'captcha', 'https://ufa.cian.ru/tmgrdfrend/showcaptcha')])
def test_block_and_cooldown(status,body,final_url):
    c=CianCollector()
    c.page=AsyncMock()
    c.page.url=final_url
    c.page.goto.return_value=SimpleNamespace(status=status,all_headers=AsyncMock(return_value={'retry-after':'7200'}))
    c.page.content.return_value=body
    with pytest.raises(RuntimeError,match='blocked'):
        asyncio.run(c._load_page(URL))
    assert c.stop_requested
    with pytest.raises(RuntimeError,match='cooldown'):
        asyncio.run(c._load_page(URL))
    assert c.page.goto.await_count==1


def test_proxy_required_before_browser(settings):
    settings.CIAN_PROXY_URL=''
    c=collector([])
    with pytest.raises(CollectionError,match='direct fallback'):
        asyncio.run(c.collect(SEARCH,mode='fast'))
    c._start.assert_not_awaited()


def test_browser_always_uses_proxy_and_closes():
    from unittest.mock import patch
    driver=AsyncMock()
    start=AsyncMock(return_value=driver)
    async def run():
        async with CianCollector() as c:
            await c._start()
            await c._start()
    with patch('collectors.cian.collector.async_playwright',return_value=SimpleNamespace(start=start)):
        asyncio.run(run())
    assert driver.chromium.launch_persistent_context.call_args.kwargs['proxy']=={'server':'socks5://localhost:1080'}
    driver.chromium.launch_persistent_context.assert_awaited_once()
    driver.chromium.launch_persistent_context.return_value.close.assert_awaited_once()
    driver.stop.assert_awaited_once()
