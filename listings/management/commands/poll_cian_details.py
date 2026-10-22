from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from collectors.cian.collector import CianCollector
from collectors.routing import resolve_profile
from listings.models import CianDetailPollProgress, CianDetailPollState, Listing
from listings.services.cian_detail_persistence import save_cian_detail
from listings.services.detail_progress import (
    clear_detail_current,
    mark_detail_completed,
    prepare_detail_cycle,
    set_detail_current,
)
from listings.services.detail_polling import run_detail_batch, select_detail_candidates
from listings.services.polling import cian_polling_allowed

LOCK_ID = 724198501


def save_detail(listing_id, detail):
    """Сохраняет detail CIAN через сервисный слой."""
    return save_cian_detail(listing_id, detail)


def prepare_cycle():
    """Готовит цикл CIAN через общий progress-сервис."""
    return prepare_detail_cycle(CianDetailPollProgress, Listing, "cian")


def set_current(progress_id, listing_id):
    """Запоминает текущую карточку CIAN."""
    return set_detail_current(CianDetailPollProgress, progress_id, listing_id)


def mark_completed(progress_id, listing_id):
    """Отмечает объявление CIAN завершённым."""
    return mark_detail_completed(CianDetailPollProgress, progress_id, listing_id)


def clear_current(progress_id):
    """Очищает текущую карточку CIAN после ошибки."""
    return clear_detail_current(CianDetailPollProgress, progress_id)


class Command(BaseCommand):
    help = "Cautiously poll a small, oldest-first batch of saved CIAN detail pages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=settings.CIAN_DETAIL_POLL_BATCH_SIZE)
        parser.add_argument("--headless", action="store_true")
        parser.add_argument("--profile", help="Polling profile code; otherwise the enabled CIAN detail profile is used")

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 20:
            raise CommandError("--limit must be between 1 and 20")
        if not cian_polling_allowed():
            self.stdout.write("CIAN detail polling is outside the daytime window")
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            if not cursor.fetchone()[0]:
                raise CommandError("Another collector is already running")
        try:
            progress_id, completed_ids = prepare_cycle()
            candidates = select_detail_candidates("cian", "cian_detail_state", completed_ids, limit)
            if not candidates:
                self.stdout.write("No active CIAN listings")
                return
            try:
                profile = resolve_profile("cian", "detail", options.get("profile"))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            async_to_sync(self.poll)(candidates, progress_id, options["headless"], profile)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    async def poll(self, candidates, progress_id, headless, profile):
        async with CianCollector(headless=headless, route=profile.route, session_mode=profile.session_mode) as collector:
            async def fetch_detail(listing):
                return await collector.poll_detail(listing)

            await run_detail_batch(
                candidates, progress_id, collector, fetch_detail, save_detail,
                CianDetailPollState, mark_completed, set_current, clear_current,
                success_message=lambda listing, detail, available: f"CIAN detail {listing.external_id}: {detail.status}, available={available}",
                source_label="CIAN detail", stdout=self.stdout, stderr=self.stderr,
                stop_message="CIAN detail polling stopped immediately after a block or network failure",
                continue_polling=cian_polling_allowed,
                continue_stop_message="CIAN daytime window ended; stopping detail batch",
            )
