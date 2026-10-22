from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from collectors.avito.api import AvitoApiCollector
from collectors.avito.detail import parse_detail_payload
from collectors.routing import resolve_profile
from listings.models import AvitoDetailPollProgress, AvitoDetailPollState, Listing
from listings.services.avito_detail_persistence import save_avito_detail
from listings.services.detail_progress import clear_detail_current, mark_detail_completed, prepare_detail_cycle, set_detail_current
from listings.services.detail_polling import run_detail_batch, select_detail_candidates

LOCK_ID = 724198502


def save_detail(listing_id, detail):
    """Сохраняет detail Avito через сервисный слой."""
    return save_avito_detail(listing_id, detail)


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
