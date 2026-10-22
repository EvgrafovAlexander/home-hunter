from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from collectors.avito.api import AvitoApiCollector
from collectors.avito.detail import parse_detail_payload
from collectors.routing import resolve_profile
from listings.models import AvitoDetailPayload, AvitoDetailPollProgress, AvitoDetailPollState, Listing, ListingSnapshot
from listings.services.detail_persistence import mark_detail_unavailable, save_detail_payload
from listings.services.detail_progress import clear_detail_current, mark_detail_completed, prepare_detail_cycle, set_detail_current
from listings.services.detail_polling import run_detail_batch, select_detail_candidates
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


def set_current(progress_id, listing_id):
    """Запоминает текущую карточку Avito."""
    return set_detail_current(AvitoDetailPollProgress, progress_id, listing_id)


def clear_current(progress_id):
    """Очищает текущую карточку Avito после ошибки."""
    return clear_detail_current(AvitoDetailPollProgress, progress_id)


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
            candidates = select_detail_candidates("avito", "avito_detail_state", completed, options["limit"])
            if not candidates:
                self.stdout.write("No active Avito listings")
                return
            async_to_sync(self.poll)(candidates, progress_id, profile)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    async def poll(self, candidates, progress_id, profile):
        async with AvitoApiCollector(profile=profile, route=profile.route) as collector:
            async def fetch_detail(listing):
                payload = await collector.poll_detail(listing.external_id)
                return parse_detail_payload(payload)

            await run_detail_batch(
                candidates, progress_id, collector, fetch_detail, save_detail,
                AvitoDetailPollState, mark_completed, set_current, clear_current,
                success_message=lambda listing, detail, _available: f"Avito detail {listing.external_id}: {detail.status}",
                source_label="Avito detail", stdout=self.stdout, stderr=self.stderr,
            )
