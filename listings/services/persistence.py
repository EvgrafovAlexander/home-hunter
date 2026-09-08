from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from collectors.base import NormalizedListing
from listings.models import Listing, ListingSearchQuery, ListingSnapshot, PriceHistory, SearchQuery
from listings.services.enrichment import calculate_price_per_sqm, location_is_visible, parse_address

SIGNIFICANT_FIELDS = (
    "price", "title", "description", "address", "district", "microdistrict", "is_visible", "rooms", "area", "floor", "floors_total",
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
    # Keep a price published by the source; otherwise calculate the same metric
    # from the two canonical fields.  `price / rooms` is price per room, not per m².
    if values["price_per_sqm"] is None:
        values["price_per_sqm"] = calculate_price_per_sqm(values["price"], values["area"])
    parsed_address = parse_address(values["address"], values["district"])
    values.update(
        address=parsed_address.address,
        district=parsed_address.district,
        microdistrict=parsed_address.microdistrict,
        is_visible=parsed_address.is_visible,
    )
    listing, created = Listing.objects.select_for_update().get_or_create(
        source=item.source, external_id=item.external_id,
        defaults={**values, "first_seen_at": observed_at, "last_seen_at": observed_at},
    )
    if not created:
        if listing.address_override:
            values["address"] = listing.address_override
        if listing.district_override:
            values["district"] = listing.district_override
        if listing.microdistrict_override:
            values["microdistrict"] = listing.microdistrict_override
        if any((listing.address_override, listing.district_override, listing.microdistrict_override)):
            values["is_visible"] = location_is_visible(
                values["address"], values["district"], values["microdistrict"],
            )
    # A missing price is not evidence that the last known price changed.
    if not created and values["price"] is None:
        values["price"] = listing.price
        if values["price_per_sqm"] is None:
            values["price_per_sqm"] = (
                calculate_price_per_sqm(values["price"], values["area"])
                or listing.price_per_sqm
            )
    if not created and not values["address"]:
        values.update(
            address=listing.address,
            district=listing.district,
            microdistrict=listing.microdistrict,
            is_visible=listing.is_visible,
        )
    address_changed = not created and listing.address != values["address"]
    changed = {key for key, value in values.items() if getattr(listing, key) != value}
    price_changed = not created and "price" in changed
    if created or price_changed:
        PriceHistory.objects.create(listing=listing, price=values["price"], observed_at=observed_at)
    if created or changed.intersection(SIGNIFICANT_FIELDS):
        snapshot = {key: str(value) if isinstance(value, Decimal) else value for key, value in values.items()}
        ListingSnapshot.objects.create(listing=listing, observed_at=observed_at, data=snapshot)
    for key, value in values.items():
        setattr(listing, key, value)
    if address_changed:
        listing.latitude = listing.longitude = None
        listing.geocode_status = listing.geocoded_at = None
    listing.last_seen_at = observed_at
    listing.is_active = True
    listing.save()
    ListingSearchQuery.objects.update_or_create(
        listing=listing, search_query=search_query,
        defaults={"last_seen_at": observed_at, "is_active": True},
        create_defaults={"first_seen_at": observed_at, "last_seen_at": observed_at, "is_active": True},
    )
    if created:
        from listings.services.deal_alerts import notify_new_deal
        transaction.on_commit(lambda listing_id=listing.pk: notify_new_deal(listing_id))
    return PersistenceResult(listing, created, not created and bool(changed), price_changed)
