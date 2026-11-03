"""Ежедневные наблюдения рынка; исходная история объявлений хранится отдельно."""

from django.db import models


class MarketDailySnapshot(models.Model):
    day = models.DateField(db_index=True)
    source = models.CharField(max_length=20)
    captured_at = models.DateTimeField()
    methodology_version = models.PositiveSmallIntegerField(default=1)
    scope_hash = models.CharField(max_length=64)
    quality = models.JSONField(default=dict)
    listings = models.JSONField(default=list)

    class Meta:
        ordering = ("day", "source")
        constraints = [models.UniqueConstraint(fields=("day", "source"), name="unique_market_day_source")]
