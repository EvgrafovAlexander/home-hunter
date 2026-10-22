"""Сохраняет detail-ответы Avito и изменения карточки в ORM."""

from django.db import transaction
from django.utils import timezone

from listings.models import AvitoDetailPayload, AvitoDetailPollState, Listing
from listings.services.detail_persistence import mark_detail_published, mark_detail_unavailable, save_detail_payload


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
    mark_detail_published(listing, state, detail_data, observed, changed, "avito_detail")
    return True
