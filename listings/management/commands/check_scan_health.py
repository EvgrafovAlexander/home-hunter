from django.core.management.base import BaseCommand

from listings.models import SearchQuery
from listings.services.scan_health import check_source_health


class Command(BaseCommand):
    help = "Check scan freshness and send Telegram alerts on health state changes."

    def handle(self, *args, **options):
        for source, label in SearchQuery.Source.choices:
            health = check_source_health(source)
            self.stdout.write(f"{label}: {health.state}")
