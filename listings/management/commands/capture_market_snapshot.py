"""Ежедневное сохранение текущего состояния наблюдаемого рынка."""

from django.core.management.base import BaseCommand

from listings.services.market_trends import capture_market_snapshot


class Command(BaseCommand):
    help = "Сохранить дневной срез рынка; существующий срез за сегодня не изменяется"

    def handle(self, *args, **options):
        for snapshot in capture_market_snapshot():
            self.stdout.write(f"{snapshot.day} {snapshot.source}: {len(snapshot.listings)} объявлений")
