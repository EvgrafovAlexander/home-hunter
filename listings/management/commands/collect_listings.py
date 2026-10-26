import math

from asgiref.sync import async_to_sync, sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.conf import settings
from django.utils import timezone

from collectors.registry import get_collector
from collectors.routing import resolve_profile
from listings.models import Scan, SearchQuery
from listings.services.polling import cian_polling_allowed, polling_enabled
from listings.services.scans import run_scan
from listings.services.scan_summary import send_full_scan_summary

# Источники получают разные advisory lock, чтобы независимые запросы не ждали друг друга.
LOCK_IDS = {
    "avito": 724198501,
    "cian": 724198503,
    "domclick": 724198504,
}
LOCK_ID = LOCK_IDS["avito"]


class Command(BaseCommand):
    help = "Collect enabled searches sequentially with one Chromium browser."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, choices=["avito", "cian", "domclick"])
        parser.add_argument("--mode", required=True, choices=["fast", "full"])
        parser.add_argument("--search-id", type=int)
        parser.add_argument("--profile", help="Polling profile code; otherwise the enabled default is used")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--headed", dest="headless", action="store_false")
        group.add_argument("--headless", dest="headless", action="store_true")
        parser.set_defaults(headless=None)
        parser.add_argument("--wait-for-captcha", type=float, default=0, metavar="SECONDS",
                            help="With --headed, wait for manual captcha without reloading the page")

    def handle(self, *args, **options):
        if not polling_enabled(options["source"], options["mode"]):
            self.stdout.write(f"{options['mode'].title()} polling disabled for {options['source']}")
            return
        if options["source"] == "cian" and not cian_polling_allowed():
            self.stdout.write("CIAN polling is outside the daytime window")
            return
        if options["headless"] is None:
            options["headless"] = options["source"] not in {"cian", "domclick"}
        full_setting = f"{options["source"].upper()}_FULL_SCAN_ENABLED"
        if options["mode"] == "full" and not getattr(settings, full_setting):
            raise CommandError(f"Full scans disabled; set {full_setting}=true explicitly")
        wait = options["wait_for_captcha"]
        if not math.isfinite(wait) or wait < 0 or (wait and options["headless"]):
            raise CommandError("--wait-for-captcha requires --headed and a finite non-negative timeout")
        searches = SearchQuery.objects.filter(source=options["source"], enabled=True).order_by("pk")
        if options["search_id"] is not None:
            searches = searches.filter(pk=options["search_id"])
        searches = list(searches)
        if not searches:
            if options["search_id"] is not None:
                raise CommandError("Enabled search not found for the selected source")
            self.stdout.write("No enabled searches")
            return
        try:
            profile = resolve_profile(options["source"], options["mode"], options.get("profile"))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        # Защищаем ручные запуски в пределах источника; host timers дополнительно используют flock.
        lock_id = LOCK_IDS[options["source"]]
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise CommandError("Another collector is already running")
        full_started_at = timezone.now()
        command_error = ""
        try:
            failed = async_to_sync(self.collect_searches)(searches, options, profile)
        except Exception as exc:
            failed = 1
            command_error = f"{type(exc).__name__}: {str(exc)}"
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
            if options["mode"] in {"fast", "full"}:
                send_full_scan_summary(options["source"], full_started_at,
                                       command_error=command_error, mode=options["mode"])
        if command_error:
            raise CommandError(command_error)
        if failed:
            raise CommandError(f"{failed} scan(s) failed; inspect Scan in Admin")

    async def collect_searches(self, searches, options, profile) -> int:
        failed = 0
        # Передаём найденный профиль фабрике, чтобы сборщики изолировали
        # cookies и состояние запросов по коду профиля. Для Avito это также
        # разделяет браузерные cookies; старое назначение route/session
        # сохраняем для других сборщиков и пользовательских фабрик.
        factory_kwargs = {
            "headless": options["headless"],
            "manual_wait_seconds": options.get("wait_for_captcha", 0),
        }
        if options["source"] == "avito":
            factory_kwargs["profile"] = profile
        collector = get_collector(options["source"], **factory_kwargs)
        if options["source"] != "avito":
            collector.route = profile.route
            collector.session_mode = profile.session_mode
        async with collector:
            for search in searches:
                if options["source"] == "cian" and not cian_polling_allowed():
                    self.stdout.write("CIAN daytime window ended; stopping scan batch")
                    break
                scan = await sync_to_async(run_scan)(search, collector, mode=options["mode"], profile=profile)
                self.stdout.write(f"Scan {scan.pk}: {scan.status}, new={scan.new_items}, price_changes={scan.price_changes}")
                failed += scan.status == Scan.Status.FAILED
                if getattr(collector, "stop_requested", False):
                    self.stderr.write(f"{options["source"]}: stopping remaining searches; inspect diagnostics")
                    break
        return failed
