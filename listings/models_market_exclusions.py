"""Ручные исключения объявлений из рынка и история их возврата."""

from django.conf import settings
from django.db import models


class MarketExclusion(models.Model):
    class Reason(models.TextChoices):
        UNFINISHED = "unfinished", "Без отделки"
        MAJOR_REPAIR = "major_repair", "Требует капитального ремонта"
        BAD_DATA = "bad_data", "Ошибка данных"
        DUPLICATE = "duplicate", "Дубликат"
        OTHER = "other", "Другое"

    listing = models.ForeignKey("Listing", on_delete=models.PROTECT, related_name="market_exclusions")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    comment = models.TextField(blank=True, max_length=2000)
    excluded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="market_exclusions")
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="restored_market_exclusions")

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [models.UniqueConstraint(fields=("listing",), condition=models.Q(is_active=True),
                                               name="unique_active_market_exclusion")]
