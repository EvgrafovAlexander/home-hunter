"""Сохраняет и обогащает detail-ответы CIAN на границе ORM."""

from django.db import transaction
from django.utils import timezone

from listings.models import CianDetailPayload, CianDetailPollState, Listing, ListingSnapshot, PriceHistory
from listings.services.detail_persistence import mark_detail_unavailable, save_detail_payload
from listings.services.enrichment import calculate_price_per_sqm, location_is_visible, parse_address
from listings.services.location_directory import location_is_excluded, resolve_location
from listings.services.microdistrict_polygons import enrich_listing_from_coordinates
from listings.services.persistence import snapshot_data_from_listing


def _apply_detail_fields(listing, detail):
    """Применяет поля карточки, не затирая ручные адресные переопределения."""
    changed = []
    for field in ("price", "description", "rooms", "area", "floor", "floors_total", "built_year", "address", "district", "published_text", "image_url"):
        value = getattr(detail.listing, field)
        if field == "address" and listing.address_override:
            continue
        if field == "district" and listing.district_override:
            continue
        if value is not None and getattr(listing, field) != value:
            setattr(listing, field, value)
            changed.append(field)
    for field, value in detail.attributes.items():
        if value is not None and getattr(listing, field) != value:
            setattr(listing, field, value)
            changed.append(field)
    return changed


def _enrich_location(listing, detail, observed):
    """Обновляет координаты, адрес и видимость объявления."""
    if detail.latitude is not None and detail.longitude is not None:
        listing.latitude = detail.latitude
        listing.longitude = detail.longitude
        listing.geocode_status = "detail"
        listing.geocoded_at = observed
    parsed = parse_address(listing.address, listing.district)
    listing.address = listing.address_override or parsed.address
    listing.district = listing.district_override or parsed.district
    listing.microdistrict = listing.microdistrict_override or parsed.microdistrict
    if not listing.microdistrict_override:
        resolution = resolve_location(listing.address, listing.district, listing.microdistrict)
        if resolution.district:
            listing.district = resolution.district.name
            listing.district_ref = resolution.district
        if resolution.microdistrict:
            listing.microdistrict = resolution.microdistrict.name
            listing.microdistrict_ref = resolution.microdistrict
    listing.is_visible = location_is_visible(
        listing.address, listing.district, listing.microdistrict,
    ) and not location_is_excluded(listing.address)
    enrich_listing_from_coordinates(listing)


@transaction.atomic
def save_cian_detail(listing_id, detail):
    """Сохраняет опубликованный или недоступный detail CIAN целиком."""
    listing = Listing.objects.select_for_update().get(pk=listing_id, source="cian")
    state, _ = CianDetailPollState.objects.select_for_update().get_or_create(listing=listing)
    observed = timezone.now()
    state.last_checked_at, state.last_error = observed, ""
    if detail.status != "published":
        return mark_detail_unavailable(
            listing,
            state,
            {"status": detail.status, "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at},
            observed,
            "cian_detail",
        )
    save_detail_payload(CianDetailPayload, listing, detail.offer_data, observed)
    changed = _apply_detail_fields(listing, detail)
    if listing.price is not None and listing.area:
        listing.price_per_sqm = calculate_price_per_sqm(listing.price, listing.area)
    _enrich_location(listing, detail, observed)
    if "price" in changed:
        PriceHistory.objects.create(listing=listing, price=listing.price, observed_at=observed)
    detail_changed = state.detail_data.get("photo_ids") != detail.photo_ids or state.detail_data.get("edited_at") != detail.edited_at
    if changed or detail_changed:
        ListingSnapshot.objects.create(
            listing=listing,
            observed_at=observed,
            data={**snapshot_data_from_listing(listing), "cian_detail": {"photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}},
        )
    listing.last_seen_at, listing.is_active = observed, True
    listing.publication_status = Listing.PublicationStatus.PUBLISHED
    listing.save()
    state.status, state.last_available_at, state.first_unavailable_at = "published", observed, None
    state.detail_data = {"status": "published", "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}
    state.save()
    if listing.latitude is not None and listing.longitude is not None:
        from listings.services.deal_alerts import refresh_telegram_listing_score
        transaction.on_commit(lambda listing_id=listing.pk: refresh_telegram_listing_score(listing_id))
    return True
