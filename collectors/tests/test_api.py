import asyncio
from decimal import Decimal
from types import SimpleNamespace

from collectors.avito.api import api_page_url, normalize_item, extract_catalog, AvitoApiCollector


def api_profile():
    return SimpleNamespace(
        code="avito-api-test",
        settings={"api_url": "https://api.example/search"},
        cookie_strategy="own_cookies",
    )


def test_api_page_url_replaces_page_parameter():
    assert api_page_url("https://api.example/search?p=9&s=104", 2) == (
        "https://api.example/search?s=104&page=2"
    )


def test_normalize_api_item():
    item = {
        "id": 123,
        "urlPath": "/ufa/kvartiry/flat_123",
        "title": "2-к. квартира, 58,6 м², 6/12 эт.",
        "description": "Светлая квартира",
        "priceDetailed": {"value": 6500000},
        "addressDetailed": {"locationName": "Уфа, ул. Ленина, 1"},
        "gallery": {"imageLargeUrl": "https://images.example/1.jpg"},
    }
    listing = normalize_item(item)
    assert listing.external_id == "123"
    assert listing.url == "https://www.avito.ru/ufa/kvartiry/flat_123"
    assert listing.price == 6500000
    assert listing.rooms == 2 and listing.area == Decimal("58.6")
    assert listing.image_url == "https://images.example/1.jpg"


def test_api_collector_reads_catalog_without_affecting_browser_collector(settings):
    settings.AVITO_FAST_SCAN_PAGES = 1
    settings.AVITO_PAGE_DELAY_SECONDS = 0
    collector = AvitoApiCollector(profile=api_profile())
    collector._api_url = lambda url: "https://api.example/search"
    collector._request = lambda url: {"result": {"catalog": {"items": [{
        "id": 123,
        "urlPath": "/ufa/flat_123",
        "title": "2-к. квартира, 58,6 м², 6/12 эт.",
        "priceDetailed": {"value": 6500000},
    }]}}}
    result = asyncio.run(collector.collect(
        SimpleNamespace(name="API test", url="https://www.avito.ru/ufa/kvartiry"), mode="fast",
    ))
    assert result.complete and len(result.listings) == 1
    assert extract_catalog({"catalog": {"items": []}})["items"] == []
