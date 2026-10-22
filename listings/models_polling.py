"""Модели состояния detail-опросов и полных циклов CIAN/Avito."""

from django.db import models
from django.utils import timezone

from .models import Listing, SearchQuery


class CianFullScanCheckpoint(models.Model):
    """Состояние постраничного полного прохода CIAN."""

    search_query = models.OneToOneField(SearchQuery, on_delete=models.CASCADE, related_name="cian_full_checkpoint")
    search_url = models.TextField()
    next_page = models.PositiveIntegerField(default=1)
    expected_total = models.PositiveIntegerField(null=True, blank=True)
    external_ids = models.JSONField(default=list)
    cycle_started_at = models.DateTimeField(default=timezone.now)
    total_changes = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class CianDetailPollState(models.Model):
    """Последнее техническое состояние detail-страницы CIAN."""

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Не проверено"
        PUBLISHED = "published", "Активно"
        UNAVAILABLE = "unavailable", "Недоступно"

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="cian_detail_state")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_available_at = models.DateTimeField(null=True, blank=True)
    first_unavailable_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    detail_data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CianDetailPayload(models.Model):
    """Последний полный структурированный payload detail-страницы CIAN."""

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="cian_detail_payload")
    payload = models.JSONField()
    sha256 = models.CharField(max_length=64)
    first_received_at = models.DateTimeField(default=timezone.now)
    last_received_at = models.DateTimeField(default=timezone.now)


class CianDetailPollProgress(models.Model):
    """Прогресс справедливого прохода по активным объявлениям CIAN."""

    source = models.CharField(max_length=20, unique=True, default=SearchQuery.Source.CIAN)
    cycle_started_at = models.DateTimeField(default=timezone.now)
    cycle_total = models.PositiveIntegerField(default=0)
    completed_listing_ids = models.JSONField(default=list, blank=True)
    current_listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    current_started_at = models.DateTimeField(null=True, blank=True)
    last_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AvitoDetailPollState(models.Model):
    """Последнее техническое состояние второго этапа Avito API."""

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Не проверено"
        PUBLISHED = "published", "Активно"
        UNAVAILABLE = "unavailable", "Недоступно"

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="avito_detail_state")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_available_at = models.DateTimeField(null=True, blank=True)
    first_unavailable_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    detail_data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AvitoDetailPayload(models.Model):
    """Последний полный JSON, полученный от item endpoint Avito."""

    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="avito_detail_payload")
    payload = models.JSONField()
    sha256 = models.CharField(max_length=64)
    first_received_at = models.DateTimeField(default=timezone.now)
    last_received_at = models.DateTimeField(default=timezone.now)


class AvitoDetailPollProgress(models.Model):
    """Прогресс прохода по активным объявлениям Avito."""

    source = models.CharField(max_length=20, unique=True, default=SearchQuery.Source.AVITO)
    cycle_started_at = models.DateTimeField(default=timezone.now)
    cycle_total = models.PositiveIntegerField(default=0)
    completed_listing_ids = models.JSONField(default=list, blank=True)
    current_listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    current_started_at = models.DateTimeField(null=True, blank=True)
    last_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
