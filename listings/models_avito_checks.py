"""История ручных проверок доступности и цены объявлений Avito."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AvitoManualCheck(models.Model):
    """Неизменяемый результат ручной проверки с защитой от повторной отправки."""

    class Action(models.TextChoices):
        AVAILABLE = "available", "Доступно"
        UNAVAILABLE = "unavailable", "Недоступно"
        PRICE = "price", "Обновить цену"

    listing = models.ForeignKey("Listing", on_delete=models.PROTECT, related_name="avito_manual_checks")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=16, choices=Action.choices)
    checked_at = models.DateTimeField(default=timezone.now)
    old_price = models.BigIntegerField(null=True)
    new_price = models.BigIntegerField(null=True)
    operation_id = models.UUIDField(unique=True)

    class Meta:
        ordering = ("-checked_at", "-pk")
        indexes = [models.Index(fields=("listing", "-checked_at"), name="avito_check_listing_time")]
