import math

from asgiref.sync import async_to_sync, sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.conf import settings

from collectors.registry import get_collector
from listings.models import Scan, SearchQuery
from listings.services.scans import run_scan

LOCK_ID = 724198501


class Command(BaseCommand):
    help = "Collect enabled searches sequentially with one Chromium browser."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, choices=["avito", "cian"])
        parser.add_argument("--mode", required=True, choices=["fast", "full"])
        parser.add_argument("--search-id", type=int)
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--headed", dest="headless", action="store_false")
        group.add_argument("--headless", dest="headless", action="store_true")
        parser.set_defaults(headless=None)
        parser.add_argument("--wait-for-captcha", type=float, default=0, metavar="SECONDS",
                            help="With --headed, wait for manual captcha without reloading the page")

    def handle(self, *args, **options):
        if options["headless"] is None:
            options["headless"] = options["source"] != "cian"
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
        # Also protect manual invocations; host timers additionally share flock.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            if not cursor.fetchone()[0]:
                raise CommandError("Another collector is already running")
        try:
            failed = async_to_sync(self.collect_searches)(searches, options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])
        if failed:
            raise CommandError(f"{failed} scan(s) failed; inspect Scan in Admin")

    async def collect_searches(self, searches, options) -> int:
        failed = 0
        async with get_collector(options["source"], headless=options["headless"],
                                 manual_wait_seconds=options.get("wait_for_captcha", 0)) as collector:
            for search in searches:
                scan = await sync_to_async(run_scan)(search, collector, mode=options["mode"])
                self.stdout.write(f"Scan {scan.pk}: {scan.status}, new={scan.new_items}, price_changes={scan.price_changes}")
                failed += scan.status == Scan.Status.FAILED
                if getattr(collector, "stop_requested", False):
                    self.stderr.write(f"{options["source"]}: stopping remaining searches; inspect diagnostics")
                    break
        return failed
