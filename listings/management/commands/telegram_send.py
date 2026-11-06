import time

from django.core.management.base import BaseCommand
from django.conf import settings

from listings.services.deal_alerts import notify_new_listing
from listings.services.telegram_delivery import claim_listing_alerts, mark_listing_alert_failed
from listings.services.viewing_telegram import deliver_viewing_publications


class Command(BaseCommand):
    help = "Deliver queued Telegram listing publications with retries."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=600, help="Seconds between queue checks (default: 600).")
        parser.add_argument("--batch-size", type=int, default=10)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.TG_BOT_TOKEN:
            self.stderr.write("TG_BOT_TOKEN is not configured")
            return
        interval = max(60, options["interval"])
        batch_size = max(1, options["batch_size"])
        while True:
            claimed = claim_listing_alerts(batch_size, with_attempts=True)
            for alert_id, attempt in claimed:
                try:
                    notify_new_listing(0, alert_id=alert_id, attempt=attempt)
                except Exception as exc:  # одна плохая карточка не должна остановить очередь
                    mark_listing_alert_failed(alert_id, f"{type(exc).__name__}: {exc}", attempt=attempt)
                    self.stderr.write(f"Telegram listing alert {alert_id} failed: {type(exc).__name__}")
            deliver_viewing_publications(batch_size)
            if options["once"]:
                return
            time.sleep(interval)
