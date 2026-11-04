"""Единые исключения из рынка, подбора и проверок; без HTTP и сетевых вызовов."""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from listings.models import Listing, ListingReview, MarketExclusion, TelegramListingAlert


def excluded_listing_ids():
    """Возвращает подзапрос ID текущих исключений независимо от личных скрытий."""
    return MarketExclusion.objects.filter(is_active=True).values_list("listing_id", flat=True)


def eligible_market_queryset(queryset):
    """Убирает действующие исключения из рынка или рабочей очереди любого источника."""
    return queryset.exclude(pk__in=excluded_listing_ids())


def is_market_excluded(listing):
    """Проверяет текущий допуск объявления для одиночного расчёта."""
    return MarketExclusion.objects.filter(listing_id=listing.pk, is_active=True).exists()


@transaction.atomic
def exclude_from_market(actor, listing_id, reason, comment=""):
    """Идемпотентно исключает объявление; повтор не перезаписывает причину и автора."""
    if not actor.is_active or not actor.is_staff:
        raise PermissionDenied
    listing = Listing.objects.select_for_update().get(pk=listing_id)
    active = MarketExclusion.objects.filter(listing=listing, is_active=True).first()
    if active:
        return active, False
    exclusion = MarketExclusion(listing=listing, reason=reason, comment=comment, excluded_by=actor)
    exclusion.full_clean()
    exclusion.save()
    TelegramListingAlert.objects.filter(listing=listing, delivery_status__in=("pending", "awaiting_geocode", "failed")).update(
        delivery_status="skipped", filter_reason="excluded from market statistics", filter_checked_at=timezone.now(),
        next_attempt_at=None, locked_until=None,
    )
    return exclusion, True


@transaction.atomic
def restore_to_market(actor, listing_id, exclusion_id):
    """Возвращает конкретное исключение в рынок, сохраняя его историю и автора возврата."""
    if not actor.is_active or not actor.is_staff:
        raise PermissionDenied
    Listing.objects.select_for_update().get(pk=listing_id)
    exclusion = MarketExclusion.objects.filter(pk=exclusion_id, listing_id=listing_id, is_active=True).first()
    if exclusion is None:
        raise ValidationError("Исключение уже изменено. Обновите страницу.")
    exclusion.is_active = False
    exclusion.restored_at = timezone.now()
    exclusion.restored_by = actor
    exclusion.save(update_fields=("is_active", "restored_at", "restored_by"))
    return exclusion


def unfinished_review_candidates(author_id):
    """Выбирает оценку 1 с проверенными однозначными формулировками об отсутствии отделки."""
    phrases = {
        "отсутствует отделка", "нет отделки", "без отделки",
        "отсутствует отделка, это первичка", "отсутствует отделка, фактически это первичка",
        "отсутствует отделка, это первичка, требует капитального ремонта",
        "без отделки, это фактически первичка", "первичка, нет отделки",
        "это первичка без отделки", "это первичка, нет отделки",
    }
    reviews = ListingReview.objects.filter(author_id=author_id, rating=1).order_by("pk")
    return [review for review in reviews if any(
        re.sub(r"\s+", " ", text.casefold().strip().strip('.!?,;:')).strip() in phrases
        for text in (review.comment, review.deal_breaker)
    )]
