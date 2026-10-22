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


def test_web_and_telegram_use_the_same_strict_comparison_policy():
    for index in range(5):
        make_listing(f"comparison-{index}", price_per_sqm=150_000, area="60.00")
    target = make_listing("target", price_per_sqm=125_000, area="60.00")

    annotate_market_position([target])
    details = market_position_details(target)

    assert details == (17, "похожим квартирам от 55 м²", 5, 150_000)
    assert target.market_delta_pct == -details[0]
    assert target.market_reference == details[1]
    assert target.market_samples == details[2]


def test_web_and_telegram_use_the_same_area_fallback_policy():
    for index in range(3):
        make_listing(f"small-{index}", price_per_sqm=150_000, area="50.00")
    for index in range(2):
        make_listing(f"large-{index}", price_per_sqm=150_000, area="60.00")
    target = make_listing("target", price_per_sqm=125_000, area="60.00")

    annotate_market_position([target])
    details = market_position_details(target)

    assert details == (17, "похожим квартирам (включая 45–54 м²)", 5, 150_000)
    assert target.market_strict_samples == 2
    assert target.market_fallback_samples == 3
