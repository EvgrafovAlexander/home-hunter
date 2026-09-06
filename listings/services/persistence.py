from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from collectors.base import NormalizedListing
from listings.models import Listing, ListingSearchQuery, ListingSnapshot, PriceHistory, SearchQuery

SIGNIFICANT_FIELDS = (
    "price", "title", "description", "address", "district", "rooms", "area", "floor", "floors_total",
)


@dataclass
class PersistenceResult:
    listing: Listing
    created: bool
    updated: bool
    price_changed: bool


@transaction.atomic
def process_listing(
    search_query: SearchQuery, normalized_listing: NormalizedListing, observed_at: datetime,
) -> PersistenceResult:
    item = normalized_listing
    if item.source != search_query.source or not item.external_id:
        raise ValueError("Listing source/id does not match search")
    values = asdict(item)
    values.pop("raw_data")
    values.pop("source")
    values.pop("external_id")
    listing, created = Listing.objects.select_for_update().get_or_create(
        source=item.source, external_id=item.external_id,
        defaults={**values, "first_seen_at": observed_at, "last_seen_at": observed_at},
    )
    # A missing price is not evidence that the last known price changed.
    if not created and values["price"] is None:
        values["price"] = listing.price
    changed = {key for key, value in values.items() if getattr(listing, key) != value}
    price_changed = not created and "price" in changed
    if created or price_changed:
        PriceHistory.objects.create(listing=listing, price=values["price"], observed_at=observed_at)
    if created or changed.intersection(SIGNIFICANT_FIELDS):
        snapshot = {key: str(value) if isinstance(value, Decimal) else value for key, value in values.items()}
        ListingSnapshot.objects.create(listing=listing, observed_at=observed_at, data=snapshot)
    for key, value in values.items():
        setattr(listing, key, value)
    listing.last_seen_at = observed_at
    listing.is_active = True
    listing.save()
    ListingSearchQuery.objects.update_or_create(
        listing=listing, search_query=search_query,
        defaults={"last_seen_at": observed_at, "is_active": True},
        create_defaults={"first_seen_at": observed_at, "last_seen_at": observed_at, "is_active": True},
    )
    return PersistenceResult(listing, created, not created and bool(changed), price_changed)
