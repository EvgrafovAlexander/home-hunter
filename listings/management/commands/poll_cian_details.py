import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from collectors.cian.collector import CianCollector
from listings.models import CianDetailPollState, Listing, ListingSnapshot, PriceHistory
from listings.services.persistence import snapshot_data_from_listing

LOCK_ID = 724198501


@transaction.atomic
def save_detail(listing_id, detail):
    listing = Listing.objects.select_for_update().get(pk=listing_id, source="cian")
    state, _ = CianDetailPollState.objects.select_for_update().get_or_create(listing=listing)
    observed = timezone.now()
    state.last_checked_at, state.last_error = observed, ""
    if detail.status != "published":
        state.status = CianDetailPollState.Status.UNAVAILABLE
        state.first_unavailable_at = state.first_unavailable_at or observed
        state.detail_data = {"status": detail.status, "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}
        state.save()
        return False
    item = detail.listing
    changed = []
    for field in ("price", "title", "description", "rooms", "area", "floor", "floors_total", "built_year", "address", "district", "published_text", "image_url"):
        value = getattr(item, field)
        if value is not None and getattr(listing, field) != value:
            setattr(listing, field, value)
            changed.append(field)
    if item.price is not None and listing.area:
        listing.price_per_sqm = round(item.price / float(listing.area))
    if "price" in changed:
        PriceHistory.objects.create(listing=listing, price=listing.price, observed_at=observed)
    detail_changed = state.detail_data.get("photo_ids") != detail.photo_ids or state.detail_data.get("edited_at") != detail.edited_at
    if changed or detail_changed:
        ListingSnapshot.objects.create(listing=listing, observed_at=observed,
                                       data={**snapshot_data_from_listing(listing), "cian_detail": {"photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}})
    listing.last_seen_at, listing.is_active = observed, True
    listing.save()
    state.status, state.last_available_at, state.first_unavailable_at = "published", observed, None
    state.detail_data = {"status": "published", "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}
    state.save()
    return True


class Command(BaseCommand):
    help = "Cautiously poll a small, oldest-first batch of saved CIAN detail pages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=settings.CIAN_DETAIL_POLL_BATCH_SIZE)
        parser.add_argument("--headless", action="store_true")

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 20:
            raise CommandError("--limit must be between 1 and 20")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            if not cursor.fetchone()[0]:
                raise CommandError("Another collector is already running")
        try:
            candidates = list(Listing.objects.filter(source="cian", is_active=True).select_related("cian_detail_state").order_by(F("cian_detail_state__last_checked_at").asc(nulls_first=True), "pk")[:limit])
            if not candidates:
                self.stdout.write("No active CIAN listings")
                return
            async_to_sync(self.poll)(candidates, options["headless"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    async def poll(self, candidates, headless):
        async with CianCollector(headless=headless) as collector:
            for listing in candidates:
                try:
                    detail = await collector.poll_detail(listing)
                    available = await sync_to_async(save_detail)(listing.pk, detail)
                    self.stdout.write(f"CIAN detail {listing.external_id}: {detail.status}, available={available}")
                except Exception as exc:
                    await sync_to_async(CianDetailPollState.objects.update_or_create)(listing=listing, defaults={"last_error": f"{type(exc).__name__}: {exc}"[:1000]})
                    self.stderr.write(f"CIAN detail {listing.external_id}: {type(exc).__name__}")
                    if collector.stop_requested:
                        self.stderr.write("CIAN detail polling stopped immediately after a block or network failure")
                        break
