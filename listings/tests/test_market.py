from decimal import Decimal

import pytest

from listings.models import Listing
from listings.services.market import (
    annotate_market_position,
    market_position_details,
)


pytestmark = pytest.mark.django_db


def make_listing(external_id, *, price_per_sqm, area="50.00", microdistrict="Центр"):
    return Listing.objects.create(
        source="cian",
        external_id=external_id,
        url=f"https://example.test/{external_id}",
        title=external_id,
        price=int(Decimal(price_per_sqm) * Decimal(area)),
        price_per_sqm=price_per_sqm,
        rooms=2,
        area=area,
        district="Кировский",
        microdistrict=microdistrict,
    )


def test_market_position_details_uses_same_room_similar_area_group():
    for index in range(5):
        make_listing(f"comparison-{index}", price_per_sqm=150_000)
    target = make_listing("target", price_per_sqm=125_000)

    assert market_position_details(target) == (17, "похожим квартирам", 5, 150_000)


def test_annotate_market_position_excludes_other_microdistricts():
    for index in range(5):
        make_listing(f"comparison-{index}", price_per_sqm=150_000)
    make_listing("other-location", price_per_sqm=300_000, microdistrict="Затон")
    target = make_listing("target", price_per_sqm=125_000)

    annotate_market_position([target])

    assert target.market_delta_pct == -17
    assert target.market_reference == "похожим квартирам"
    assert target.market_samples == 5
