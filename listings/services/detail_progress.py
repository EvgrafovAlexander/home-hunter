"""Общие операции для циклов detail-опроса."""

from django.db import transaction
from django.utils import timezone


@transaction.atomic
def prepare_detail_cycle(progress_model, listing_model, source):
    """Готовит новый проход и сохраняет только активные объявления."""
    active_ids = list(
        listing_model.objects.filter(source=source, is_active=True).values_list("pk", flat=True)
    )
    progress, _ = progress_model.objects.select_for_update().get_or_create(source=source)
    active_set = set(active_ids)
    completed = [pk for pk in progress.completed_listing_ids if pk in active_set]
    if progress.cycle_total == 0 or len(completed) >= len(active_ids):
        progress.cycle_started_at = timezone.now()
        completed = []
    progress.cycle_total = len(active_ids)
    progress.completed_listing_ids = completed
    progress.current_listing = None
    progress.current_started_at = None
    progress.save()
    return progress.pk, completed


@transaction.atomic
def mark_detail_completed(progress_model, progress_id, listing_id):
    """Добавляет объявление в completed и закрывает текущую обработку."""
    progress = progress_model.objects.select_for_update().get(pk=progress_id)
    completed = list(progress.completed_listing_ids)
    if listing_id not in completed:
        completed.append(listing_id)
    progress.completed_listing_ids = completed
    progress.current_listing = None
    progress.current_started_at = None
    progress.last_completed_at = timezone.now()
    progress.save()


@transaction.atomic
def set_detail_current(progress_model, progress_id, listing_id):
    """Запоминает объявление, которое сейчас обрабатывается."""
    progress = progress_model.objects.select_for_update().get(pk=progress_id)
    progress.current_listing_id = listing_id
    progress.current_started_at = timezone.now()
    progress.save(update_fields=("current_listing", "current_started_at", "updated_at"))


@transaction.atomic
def clear_detail_current(progress_model, progress_id):
    """Очищает текущее объявление после ошибки обработки."""
    progress = progress_model.objects.select_for_update().get(pk=progress_id)
    progress.current_listing = None
    progress.current_started_at = None
    progress.save(update_fields=("current_listing", "current_started_at", "updated_at"))
