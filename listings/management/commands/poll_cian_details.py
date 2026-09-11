import asyncio
import hashlib
import json

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from collectors.cian.collector import CianCollector
from listings.models import (CianDetailPayload, CianDetailPollProgress, CianDetailPollState, Listing,
                             ListingSnapshot, PriceHistory)
from listings.services.persistence import snapshot_data_from_listing

LOCK_ID = 724198501


def save_detail_payload(listing, offer_data, observed):
    """Keep one complete response per listing without rewriting unchanged JSON."""
    encoded = json.dumps(offer_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    checksum = hashlib.sha256(encoded).hexdigest()
    payload, created = CianDetailPayload.objects.select_for_update().get_or_create(
        listing=listing,
        defaults={
            "payload": offer_data,
            "sha256": checksum,
            "first_received_at": observed,
            "last_received_at": observed,
        },
    )
    if created:
        return
    if payload.sha256 != checksum:
        payload.payload = offer_data
        payload.sha256 = checksum
        payload.last_received_at = observed
        payload.save(update_fields=("payload", "sha256", "last_received_at"))
    else:
        payload.last_received_at = observed
        payload.save(update_fields=("last_received_at",))


@transaction.atomic
def save_detail(listing_id, detail):
    listing = Listing.objects.select_for_update().get(pk=listing_id, source="cian")
    state, _ = CianDetailPollState.objects.select_for_update().get_or_create(listing=listing)
    observed = timezone.now()
    state.last_checked_at, state.last_error = observed, ""
    save_detail_payload(listing, detail.offer_data, observed)
    if detail.status != "published":
        state.status = CianDetailPollState.Status.UNAVAILABLE
        state.first_unavailable_at = state.first_unavailable_at or observed
        state.detail_data = {"status": detail.status, "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}
        state.save()
        if listing.publication_status != Listing.PublicationStatus.UNAVAILABLE or listing.is_active:
            listing.publication_status = Listing.PublicationStatus.UNAVAILABLE
            listing.is_active = False
            ListingSnapshot.objects.create(
                listing=listing, observed_at=observed,
                data={**snapshot_data_from_listing(listing), "cian_detail": state.detail_data},
            )
            listing.save(update_fields=("publication_status", "is_active", "updated_at"))
        return False
    item = detail.listing
    changed = []
    # Search cards provide a stable marketing title.  Detail pages do not
    # always expose one, so their technical fallback must never overwrite it.
    for field in ("price", "description", "rooms", "area", "floor", "floors_total", "built_year", "address", "district", "published_text", "image_url"):
        value = getattr(item, field)
        if value is not None and getattr(listing, field) != value:
            setattr(listing, field, value)
            changed.append(field)
    for field, value in detail.attributes.items():
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
    listing.publication_status = Listing.PublicationStatus.PUBLISHED
    listing.save()
    state.status, state.last_available_at, state.first_unavailable_at = "published", observed, None
    state.detail_data = {"status": "published", "photo_ids": detail.photo_ids, "photos": detail.photos, "edited_at": detail.edited_at}
    state.save()
    return True


@transaction.atomic
def prepare_cycle():
    active_ids = list(Listing.objects.filter(source="cian", is_active=True).values_list("pk", flat=True))
    progress, _ = CianDetailPollProgress.objects.select_for_update().get_or_create(source="cian")
    active_set = set(active_ids)
    completed = [pk for pk in progress.completed_listing_ids if pk in active_set]
    # Start the next pass only after the dashboard had a chance to show the
    # completed one.  New or reactivated cards join the following fair pass.
    if progress.cycle_total == 0 or len(completed) >= progress.cycle_total:
        progress.cycle_started_at = timezone.now()
        progress.cycle_total = len(active_ids)
        completed = []
    progress.completed_listing_ids = completed
    progress.current_listing = None
    progress.current_started_at = None
    progress.save()
    return progress.pk, completed


@transaction.atomic
def set_current(progress_id, listing_id):
    progress = CianDetailPollProgress.objects.select_for_update().get(pk=progress_id)
    progress.current_listing_id = listing_id
    progress.current_started_at = timezone.now()
    progress.save(update_fields=("current_listing", "current_started_at", "updated_at"))


@transaction.atomic
def mark_completed(progress_id, listing_id):
    progress = CianDetailPollProgress.objects.select_for_update().get(pk=progress_id)
    completed = list(progress.completed_listing_ids)
    if listing_id not in completed:
        completed.append(listing_id)
    progress.completed_listing_ids = completed
    progress.current_listing = None
    progress.current_started_at = None
    progress.last_completed_at = timezone.now()
    progress.save()


@transaction.atomic
def clear_current(progress_id):
    progress = CianDetailPollProgress.objects.select_for_update().get(pk=progress_id)
    progress.current_listing = None
    progress.current_started_at = None
    progress.save(update_fields=("current_listing", "current_started_at", "updated_at"))


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
            progress_id, completed_ids = prepare_cycle()
            candidates = list(Listing.objects.filter(source="cian", is_active=True).exclude(pk__in=completed_ids).select_related("cian_detail_state").order_by(F("cian_detail_state__last_checked_at").asc(nulls_first=True), "pk")[:limit])
            if not candidates:
                self.stdout.write("No active CIAN listings")
                return
            async_to_sync(self.poll)(candidates, progress_id, options["headless"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    async def poll(self, candidates, progress_id, headless):
        async with CianCollector(headless=headless) as collector:
            for listing in candidates:
                try:
                    await sync_to_async(set_current)(progress_id, listing.pk)
                    detail = await collector.poll_detail(listing)
                    available = await sync_to_async(save_detail)(listing.pk, detail)
                    await sync_to_async(mark_completed)(progress_id, listing.pk)
                    self.stdout.write(f"CIAN detail {listing.external_id}: {detail.status}, available={available}")
                except Exception as exc:
                    await sync_to_async(clear_current)(progress_id)
                    await sync_to_async(CianDetailPollState.objects.update_or_create)(listing=listing, defaults={"last_error": f"{type(exc).__name__}: {exc}"[:1000]})
                    self.stderr.write(f"CIAN detail {listing.external_id}: {type(exc).__name__}")
                    if collector.stop_requested:
                        self.stderr.write("CIAN detail polling stopped immediately after a block or network failure")
                        break
