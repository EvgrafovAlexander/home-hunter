"""Run via manage.py shell in collector image; real Chromium, intercepted fixture URLs."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from collectors.avito.collector import AvitoCollector
from listings.models import Listing, Scan, SearchQuery

HTML = """<html><body><div data-marker="item" data-item-id="docker-smoke">
<a data-marker="item-title" href="/flat_docker-smoke">2-к. квартира, 58,6 м², 6/12 эт.</a>
<div data-marker="item-price"><meta itemprop="price" content="6500000"></div>
<div data-marker="item-description">Fixture</div></div></body></html>"""
EMPTY = '<div data-marker="search-results/empty">Ничего не найдено</div>'
original_start = AvitoCollector._start
browsers = []


async def start_with_fixture(self):
    await original_start(self)
    browsers.append(self.browser)

    async def serve(route):
        page = int(parse_qs(urlsplit(route.request.url).query).get("p", ["1"])[0])
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=HTML if page == 1 else EMPTY)
    await self.page.route("**/*", serve)


assert not Listing.objects.filter(external_id="docker-smoke").exists()
search = SearchQuery.objects.create(name="Docker smoke fixture", source="avito", url="https://www.avito.ru/fixture")
try:
    with patch.object(AvitoCollector, "_start", start_with_fixture):
        for mode in ("fast", "full"):
            call_command("collect_listings", source="avito", mode=mode, search_id=search.pk)
    listing = Listing.objects.get(external_id="docker-smoke", source="avito")
    assert listing.price == 6500000
    assert listing.rooms == 2 and listing.floor == 6
    assert listing.price_history.count() == listing.snapshots.count() == 1
    assert listing.is_active
    assert Scan.objects.filter(search_query=search, status="success").count() == 2
    assert len(browsers) == 2 and all(not b.is_connected() for b in browsers)
    print("Docker smoke OK: real Chromium, fast/full, PostgreSQL, history and browser cleanup")
finally:
    Listing.objects.filter(external_id="docker-smoke", source="avito").delete()
    search.delete()
