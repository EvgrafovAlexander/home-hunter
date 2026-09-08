from datetime import timedelta

from django.utils import timezone

from listings.models import Listing, ListingSnapshot
from listings.services.change_history import change_events


def test_change_events_explains_values_and_photo(db):
    listing = Listing.objects.create(source="avito", external_id="history", url="https://example.test", title="Квартира")
    first = timezone.now() - timedelta(hours=1)
    ListingSnapshot.objects.create(listing=listing, observed_at=first, data={
        "price": 7000000, "area": "55.0", "image_url": "https://example.test/old.jpg", "is_active": True,
    })
    ListingSnapshot.objects.create(listing=listing, observed_at=timezone.now(), data={
        "price": 6800000, "area": "55.0", "image_url": "https://example.test/new.jpg", "is_active": False,
    })

    events = change_events(list(ListingSnapshot.objects.filter(listing=listing).order_by("-observed_at")))

    assert events[-1]["initial"]
    labels = [change["label"] for change in events[0]["changes"]]
    assert "Цена" in labels
    assert "Главное фото обновлено" in labels
    assert "Статус" in labels
