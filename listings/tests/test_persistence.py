from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import pytest
from django.utils import timezone
from collectors.base import NormalizedListing
from listings.models import Listing, ListingSearchQuery, ListingSnapshot, PriceHistory, SearchQuery
from listings.services.persistence import process_listing

pytestmark = pytest.mark.django_db


@pytest.fixture
def search():
    return SearchQuery.objects.create(name="Test", source="avito", url="https://www.avito.ru/ufa/kvartiry")


@pytest.fixture
def item():
    return NormalizedListing(source="avito", external_id="1", url="https://www.avito.ru/1",
                             title="2-к. квартира", price=6500000, area=Decimal("58.60"), description="Nice")


def test_initial_and_unchanged(search, item):
    now = timezone.now()
    first = process_listing(search, item, now)
    assert first.created and not first.updated and not first.price_changed
    later = now + timedelta(hours=1)
    second = process_listing(search, item, later)
    assert not second.created and not second.updated
    assert Listing.objects.count() == PriceHistory.objects.count() == ListingSnapshot.objects.count() == 1
    assert second.listing.first_seen_at == now
    assert second.listing.last_seen_at == later
    relation = ListingSearchQuery.objects.get()
    assert relation.first_seen_at == now and relation.last_seen_at == later


def test_price_and_description(search, item):
    process_listing(search, item, timezone.now())
    changed = replace(item, price=6000000)
    result = process_listing(search, changed, timezone.now())
    assert result.price_changed and result.updated
    assert PriceHistory.objects.count() == ListingSnapshot.objects.count() == 2
    process_listing(search, replace(changed, description="Renovated"), timezone.now())
    assert PriceHistory.objects.count() == 2
    assert ListingSnapshot.objects.count() == 3


def test_multiple_searches(search, item):
    other = SearchQuery.objects.create(name="Other", source="avito", url=search.url)
    process_listing(search, item, timezone.now())
    process_listing(other, item, timezone.now())
    assert Listing.objects.count() == 1
    assert ListingSearchQuery.objects.count() == 2
    assert PriceHistory.objects.count() == ListingSnapshot.objects.count() == 1


def test_missing_price_preserves_last_known(search, item):
    process_listing(search, item, timezone.now())
    result = process_listing(search, replace(item, price=None), timezone.now())
    assert result.listing.price == item.price
    assert PriceHistory.objects.count() == 1


def test_enriches_missing_price_per_sqm_and_district(search, item):
    result = process_listing(search, replace(
        item, price_per_sqm=None, address="ул. Ленина, 1 · 4,7 · 9 отзывов р-н Кировский",
    ), timezone.now())
    assert result.listing.price_per_sqm == 110922
    assert result.listing.district == "Кировский"
    assert result.listing.address == "ул. Ленина, 1"


def test_parses_cian_address_and_hides_dyomsky(search, item):
    result = process_listing(search, replace(
        item, address=("Республика Башкортостан, Уфа, р-н Дёмский, мкр. Дема, "
                       "Дагестанская улица, 33"),
    ), timezone.now())
    assert result.listing.address == "Дагестанская улица, 33"
    assert result.listing.district == "Дёмский"
    assert result.listing.microdistrict == "Дема"
    assert not result.listing.is_visible


def test_hides_kalininsky_district(search, item):
    result = process_listing(search, replace(
        item, address="Республика Башкортостан, Уфа, р-н Калининский, улица Ферина, 4",
    ), timezone.now())
    assert result.listing.district == "Калининский"
    assert not result.listing.is_visible


def test_hides_zaton_microdistrict(search, item):
    result = process_listing(search, replace(
        item, address="Республика Башкортостан, Уфа, мкр. Затон, Ахметова улица, 300",
    ), timezone.now())
    assert result.listing.microdistrict == "Затон"
    assert not result.listing.is_visible


def test_hides_listing_without_parsed_address(search, item):
    result = process_listing(search, replace(item, address=None), timezone.now())
    assert not result.listing.is_visible


def test_keeps_source_price_per_sqm_and_district(search, item):
    result = process_listing(search, replace(
        item, price_per_sqm=111000, district="Советский", address="р-н Кировский",
    ), timezone.now())
    assert result.listing.price_per_sqm == 111000
    assert result.listing.district == "Советский"


def test_initial_unknown_price(search, item):
    process_listing(search, replace(item, price=None), timezone.now())
    process_listing(search, item, timezone.now())
    assert list(PriceHistory.objects.order_by("observed_at").values_list("price", flat=True)) == [None, item.price]


def test_source_mismatch_is_atomic(search, item):
    with pytest.raises(ValueError):
        process_listing(search, replace(item, source="cian"), timezone.now())
    assert not Listing.objects.exists()
