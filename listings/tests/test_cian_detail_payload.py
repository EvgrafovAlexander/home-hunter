from decimal import Decimal

import pytest

from collectors.base import NormalizedListing
from collectors.cian.parser import CianDetail
from listings.management.commands.poll_cian_details import save_detail
from listings.models import CianDetailPayload, Listing


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
