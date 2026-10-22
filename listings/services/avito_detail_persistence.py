"""Сохраняет detail-ответы Avito и изменения карточки в ORM."""

from django.db import transaction
from django.utils import timezone

from listings.models import AvitoDetailPayload, AvitoDetailPollState, Listing, ListingSnapshot
from listings.services.detail_persistence import mark_detail_unavailable, save_detail_payload
from listings.services.persistence import snapshot_data_from_listing


@transaction.atomic
def save_avito_detail(listing_id, detail):
    """Сохраняет опубликованный или недоступный detail Avito."""
    listing = Listing.objects.select_for_update().get(pk=listing_id, source="avito")
    state, _ = AvitoDetailPollState.objects.select_for_update().get_or_create(listing=listing)
    observed = timezone.now()
    state.last_checked_at, state.last_error = observed, ""
    if detail.status != "published":
        return mark_detail_unavailable(
            listing, state, {"status": detail.status, "photos": []}, observed, "avito_detail",
        )
    save_detail_payload(AvitoDetailPayload, listing, detail.payload, observed)
    changed = False
    if detail.photos and listing.image_url != detail.photos[0]["full_url"]:
        listing.image_url = detail.photos[0]["full_url"]
        changed = True
    if detail.latitude is not None and detail.longitude is not None and (listing.latitude != detail.latitude or listing.longitude != detail.longitude):
        listing.latitude, listing.longitude = detail.latitude, detail.longitude
        listing.geocode_status, listing.geocoded_at = "detail", observed
        changed = True
    detail_data = {"status": "published", "photos": detail.photos, "views": detail.views}
    if state.detail_data.get("photos") != detail.photos or state.detail_data.get("views") != detail.views:
        changed = True
    if changed:
        ListingSnapshot.objects.create(
            listing=listing,
            observed_at=observed,
            data={**snapshot_data_from_listing(listing), "avito_detail": detail_data},
        )
    listing.last_seen_at, listing.is_active = observed, True
    listing.publication_status = Listing.PublicationStatus.PUBLISHED
    listing.save()
    state.status, state.last_available_at, state.first_unavailable_at = "published", observed, None
    state.detail_data = detail_data
    state.save()
    return True
