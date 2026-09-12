from decimal import Decimal

import pytest

from collectors.base import NormalizedListing
from collectors.cian.parser import CianDetail
from listings.management.commands.poll_cian_details import prepare_cycle, save_detail
from listings.models import CianDetailPayload, CianDetailPollProgress, Listing


pytestmark = pytest.mark.django_db


def test_keeps_latest_complete_offer_data_and_only_replaces_changed_payload():
    listing = Listing.objects.create(
        source="cian", external_id="330738483", url="https://ufa.cian.ru/sale/flat/330738483/",
        title="Квартира",
    )
    offer_data = {
        "offer": {
            "id": 330738483,
            "status": "published",
            "bathroom": "combined",
            "kitchenArea": "12.5",
            "repairType": "euro",
        },
    }
    detail = CianDetail(
        status="published",
        listing=NormalizedListing(source="cian", external_id="330738483", url=listing.url, title="Квартира",
                                  area=Decimal("73")),
        offer_data=offer_data,
        attributes={"kitchen_area": Decimal("12.5"), "repair_type": "euro"},
    )

    save_detail(listing.pk, detail)
    payload = CianDetailPayload.objects.get(listing=listing)
    first_hash = payload.sha256
    first_received = payload.first_received_at
    assert payload.payload == offer_data
    listing.refresh_from_db()
    assert listing.kitchen_area == Decimal("12.5")
    assert listing.repair_type == "euro"

    save_detail(listing.pk, detail)
    payload.refresh_from_db()
    assert CianDetailPayload.objects.filter(listing=listing).count() == 1
    assert payload.sha256 == first_hash
    assert payload.first_received_at == first_received

    detail.offer_data = {"offer": {"id": 330738483, "status": "published", "bathroom": "separate"}}
    save_detail(listing.pk, detail)
    payload.refresh_from_db()
    assert payload.payload["offer"]["bathroom"] == "separate"
    assert payload.sha256 != first_hash


def test_prepare_cycle_restarts_when_all_currently_active_listings_are_completed():
    first = Listing.objects.create(source="cian", external_id="first", url="https://ufa.cian.ru/sale/flat/1/")
    second = Listing.objects.create(source="cian", external_id="second", url="https://ufa.cian.ru/sale/flat/2/")
    progress = CianDetailPollProgress.objects.create(
        source="cian", cycle_total=3, completed_listing_ids=[first.pk, second.pk, 999999],
    )

    progress_id, completed = prepare_cycle()

    progress.refresh_from_db()
    assert progress_id == progress.pk
    assert completed == []
    assert progress.completed_listing_ids == []
    assert progress.cycle_total == 2
