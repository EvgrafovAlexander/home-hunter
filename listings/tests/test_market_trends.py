"""Проверки исторической выборки, недельных сравнений и HTTP-границы."""

from copy import deepcopy
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from listings.models import Listing, ListingSearchQuery, MarketDailySnapshot, Scan, SearchQuery
from listings.services.market_trends import capture_market_snapshot, market_trends_context

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 8, 23, 55, tzinfo=ZoneInfo("Asia/Yekaterinburg"))
FILTERS = {"source": "cian", "weeks": 12}
QUALITY = {"search_count": 1, "successful_searches": 1, "failed_scans": 0,
           "running_scans": 0, "coverage": "unverified"}


def row(pk, *, price=5_000_000, active=True, **kwargs):
    return {"id": pk, "price": price, "area": 50, "rooms": 2, "district": "Кировский",
            "microdistrict": "Центр", "active": active,
            "first_seen_at": (NOW - timedelta(days=30)).isoformat(), **kwargs}


def history(before, after):
    for offset in range(8):
        instant = NOW - timedelta(days=offset)
        MarketDailySnapshot.objects.create(
            day=instant.date(), source="cian", captured_at=instant, scope_hash="scope-a",
            quality=deepcopy(QUALITY), listings=deepcopy(before if offset else after),
        )


def last_point(**filters):
    return market_trends_context({**FILTERS, **filters}, today=NOW.date())["points"][-1]


def test_capture_is_immutable_and_ignores_personal_visibility():
    search = SearchQuery.objects.create(name="Поиск", source="cian", url="https://example.test/search")
    listing = Listing.objects.create(source="cian", external_id="1", url="https://example.test/1",
                                     title="Квартира", price=5_000_000, area=50, is_visible=False,
                                     first_seen_at=NOW - timedelta(days=1))
    ListingSearchQuery.objects.create(listing=listing, search_query=search)
    Scan.objects.create(search_query=search, source="cian", mode="fast", status="success",
                        started_at=NOW - timedelta(hours=1), finished_at=NOW - timedelta(minutes=5))
    with patch("listings.services.market_trends.timezone.now", return_value=NOW):
        result = capture_market_snapshot()
        listing.price = 4_000_000
        listing.save()
        repeated = capture_market_snapshot()
        call_command("capture_market_snapshot", stdout=StringIO())
    snapshot = next(item for item in result if item.source == "cian")
    assert snapshot.day == NOW.date()
    assert snapshot.listings[0]["price"] == 5_000_000
    assert snapshot.listings[0]["active"] is True
    assert snapshot.quality == QUALITY
    assert [item.pk for item in result] == [item.pk for item in repeated]
    assert MarketDailySnapshot.objects.count() == 3
    assert "https://" not in str(snapshot.quality)


def test_capture_scope_changes_and_inactive_search_relation():
    search = SearchQuery.objects.create(name="Поиск", source="cian", url="https://example.test/a")
    listing = Listing.objects.create(source="cian", external_id="1", title="Квартира", price=1,
                                     first_seen_at=NOW - timedelta(days=1))
    ListingSearchQuery.objects.create(listing=listing, search_query=search, is_active=False)
    with patch("listings.services.market_trends.timezone.now", return_value=NOW):
        capture_market_snapshot()
    original = MarketDailySnapshot.objects.get(source="cian")
    assert original.listings[0]["active"] is False
    assert original.quality["successful_searches"] == 0
    search.url = "https://example.test/b"
    search.save()
    with patch("listings.services.market_trends.timezone.now", return_value=NOW + timedelta(days=1)):
        capture_market_snapshot()
    assert MarketDailySnapshot.objects.filter(source="cian").last().scope_hash != original.scope_hash


def test_prices_flows_and_composition_are_separate():
    before = [row(1), row(2), row(3)]
    after = [row(1, price=4_500_000), row(2), row(3, active=False),
             row(4, price=10_000_000, first_seen_at=(NOW - timedelta(days=1)).isoformat())]
    history(before, after)
    point = last_point()
    assert point["active"] == 3
    assert point["samples"] == 3
    assert (point["median"], point["q25"], point["q75"]) == (100_000, 95_000, 150_000)
    assert (point["new"], point["removed"]) == (1, 1)
    assert point["matched_count"] == 2
    assert point["reduced_pct"] == 50
    assert point["matched_change"] == -5


@pytest.mark.parametrize("problem", ["gap", "scope", "version", "no_scan", "no_search"])
def test_incomplete_or_incomparable_history_does_not_invent_changes(problem):
    history([row(1)], [row(1, price=4_000_000)])
    middle = MarketDailySnapshot.objects.get(day=(NOW - timedelta(days=3)).date())
    if problem == "gap":
        middle.delete()
    else:
        if problem == "scope":
            middle.scope_hash = "scope-b"
        elif problem == "version":
            middle.methodology_version = 2
        elif problem == "no_scan":
            middle.quality["successful_searches"] = 0
        else:
            middle.quality["search_count"] = 0
        middle.save()
    point = last_point()
    assert point["median"] == 80_000
    assert point["reduced_pct"] is None
    assert point["new"] is None
    assert point["removed"] is None


def test_historical_filters_and_changed_characteristics_exclude_false_price_change():
    history([row(1), row(2), row(3)],
            [row(1, price=4_000_000, area=60), row(2, price=4_000_000, district="Советский"), row(3, price=None)])
    point = last_point(district="Кировский", area_max=55)
    assert point["active"] == 1
    assert point["samples"] == 0
    assert point["median"] is None
    assert point["matched_count"] == 0
    assert point["reduced_pct"] is None
    assert point["removed"] == 0
    context = market_trends_context(FILTERS, today=NOW.date())
    assert context["options"]["district"] == ["Кировский", "Советский"]
    assert last_point(source="avito")["active"] is None


def test_missing_week_is_not_zero_and_current_week_has_latest_point():
    instant = NOW + timedelta(days=2)
    MarketDailySnapshot.objects.create(day=instant.date(), source="cian", captured_at=instant,
                                       scope_hash="x", quality=QUALITY, listings=[row(1)])
    context = market_trends_context(FILTERS, today=instant.date())
    assert context["points"][-1]["day"] == instant.date().isoformat()
    assert context["points"][-1]["active"] == 1
    assert context["points"][-2]["active"] is None
    assert context["points"][-1]["new"] is None


def test_view_requires_login_validates_filters_and_renders_history(client):
    url = reverse("market_trends")
    assert client.get(url).status_code == 302
    user = get_user_model().objects.create_user(username="market-reader")
    client.force_login(user)
    assert "История ещё не накоплена" in client.get(url).content.decode()
    history([row(1)], [row(1)])
    with patch("listings.services.market_trends.timezone.localdate", return_value=NOW.date()):
        response = client.get(url)
    assert response.status_code == 200
    assert 'id="market-points"' in response.content.decode()
    assert 'data-market-chart="matched"' in response.content.decode()
    assert response.context["points"][-1]["median"] == 100_000
    for params in ({"source": "unknown"}, {"weeks": 999}, {"area_min": 70, "area_max": 50},
                   {"area_min": "nan"}, {"rooms": "<script>"}):
        invalid = client.get(url, params)
        assert invalid.status_code == 200
        assert invalid.context["form"].errors
        assert 'id="market-points"' not in invalid.content.decode()
