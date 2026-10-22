import asyncio

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from collectors.avito.api import AvitoApiCollector
from collectors.avito.detail import parse_detail_payload
from collectors.routing import resolve_profile
from listings.models import AvitoDetailPayload, AvitoDetailPollProgress, AvitoDetailPollState, Listing, ListingSnapshot
from listings.services.detail_persistence import mark_detail_unavailable, save_detail_payload
from listings.services.detail_progress import mark_detail_completed, prepare_detail_cycle
from listings.services.persistence import snapshot_data_from_listing

LOCK_ID = 724198502


@transaction.atomic
def save_detail(listing_id, detail):
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
        ListingSnapshot.objects.create(listing=listing, observed_at=observed, data={**snapshot_data_from_listing(listing), "avito_detail": detail_data})
    listing.last_seen_at, listing.is_active = observed, True
    listing.publication_status = Listing.PublicationStatus.PUBLISHED
    listing.save()
    state.status, state.last_available_at, state.first_unavailable_at = "published", observed, None
    state.detail_data = detail_data
    state.save()
    return True


def prepare_cycle():
    """Готовит цикл Avito через общий progress-сервис."""
    return prepare_detail_cycle(AvitoDetailPollProgress, Listing, "avito")


def mark_completed(progress_id, listing_id):
    """Отмечает объявление Avito завершённым."""
    return mark_detail_completed(AvitoDetailPollProgress, progress_id, listing_id)


class Command(BaseCommand):
    help = "Poll a small, oldest-first batch of Avito item-detail API responses."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=getattr(settings, "AVITO_DETAIL_POLL_BATCH_SIZE", 5))
        parser.add_argument("--profile", help="Mobile API Avito polling profile")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 20:
            raise CommandError("--limit must be between 1 and 20")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            if not cursor.fetchone()[0]:
                raise CommandError("Another Avito detail collector is already running")
        try:
            try:
                profile = resolve_profile("avito", "detail", options.get("profile"))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            if profile.transport != "mobile_api":
                raise CommandError("Avito detail requires a profile with Mobile API transport")
            progress_id, completed = prepare_cycle()
            candidates = list(Listing.objects.filter(source="avito", is_active=True).exclude(pk__in=completed).select_related("avito_detail_state").order_by(F("avito_detail_state__last_checked_at").asc(nulls_first=True), "pk")[:options["limit"]])
            if not candidates:
                self.stdout.write("No active Avito listings")
                return
            async_to_sync(self.poll)(candidates, progress_id, profile)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    async def poll(self, candidates, progress_id, profile):
        async with AvitoApiCollector(profile=profile, route=profile.route) as collector:
            for listing in candidates:
                try:
                    detail = parse_detail_payload(await collector.poll_detail(listing.external_id))
                    await sync_to_async(save_detail)(listing.pk, detail)
                    await sync_to_async(mark_completed)(progress_id, listing.pk)
                    self.stdout.write(f"Avito detail {listing.external_id}: {detail.status}")
                except Exception as exc:
                    state = await sync_to_async(AvitoDetailPollState.objects.update_or_create)(listing=listing, defaults={"last_error": f"{type(exc).__name__}: {exc}"[:1000]})
                    self.stderr.write(f"Avito detail {listing.external_id}: {type(exc).__name__}")
                    if collector.stop_requested:
                        break
