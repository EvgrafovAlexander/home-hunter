"""Очередь и атомарное сохранение ручных проверок Avito без обращения к площадке.

История проверок отделена от автоматического detail; уведомления вызываются после commit.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from listings.models import AvitoManualCheck, Listing, ListingSearchQuery, ListingSnapshot, PriceHistory
from listings.services.enrichment import calculate_price_per_sqm
from listings.services.persistence import snapshot_data_from_listing
from listings.services.price_alerts import notify_price_change


class StaleAvitoCheck(ValueError):
    """Объявление изменилось после открытия формы."""


def avito_check_queue(*, days=7, unavailable=False):
    """Возвращает старые активные объявления либо снятые, включая скрытые и малые площади."""
    if days not in (3, 7, 14, 30):
        raise ValueError("Выберите срок 3, 7, 14 или 30 дней.")
    queryset = Listing.objects.filter(source="avito")
    if unavailable:
        queryset = queryset.filter(Q(is_active=False) | Q(publication_status="unavailable"))
    else:
        queryset = queryset.filter(
            is_active=True, publication_status="published",
            last_seen_at__lt=timezone.now() - timedelta(days=days),
        )
    latest = AvitoManualCheck.objects.filter(listing_id=OuterRef("pk")).order_by("-checked_at", "-pk")
    return queryset.annotate(last_manual_check_at=Subquery(latest.values("checked_at")[:1])).order_by("last_seen_at", "pk")


def parse_manual_price(value):
    """Принимает положительную целую цену в рублях с необязательными пробелами."""
    compact = "".join(str(value or "").split())
    if not compact.isascii() or not compact.isdigit() or len(compact) > 13:
        raise ValueError("Укажите положительную целую цену в рублях.")
    price = int(compact)
    if not 0 < price <= 1_000_000_000_000:
        raise ValueError("Цена должна быть от 1 до 1 000 000 000 000 рублей.")
    return price


@transaction.atomic
def save_avito_manual_check(*, listing_id, author, action, operation_id, expected_version, price=None):
    """Сохраняет проверку и историю атомарно; повтор операции не меняет данные повторно."""
    if not author.is_active or not author.is_staff:
        raise PermissionError("Ручная проверка доступна сотрудникам.")
    if action not in AvitoManualCheck.Action.values:
        raise ValueError("Неизвестное действие.")
    listing = Listing.objects.select_for_update().get(pk=listing_id, source="avito")
    existing = AvitoManualCheck.objects.filter(operation_id=operation_id).first()
    if existing:
        if existing.listing_id != listing.pk or existing.author_id != author.pk:
            raise ValueError("Операция относится к другой проверке.")
        return existing, False
    if listing.updated_at.isoformat() != expected_version:
        raise StaleAvitoCheck("Объявление уже изменилось. Проверьте актуальные данные и повторите выбор.")
    old_price = listing.price
    before = (listing.is_active, listing.publication_status, listing.price, listing.price_per_sqm)
    observed = timezone.now()
    if action == AvitoManualCheck.Action.PRICE:
        listing.price = parse_manual_price(price)
        listing.price_per_sqm = calculate_price_per_sqm(listing.price, listing.area)
    available = action != AvitoManualCheck.Action.UNAVAILABLE
    listing.is_active = available
    listing.publication_status = "published" if available else "unavailable"
    if available:
        listing.last_seen_at = observed
    listing.save(update_fields=("is_active", "publication_status", "last_seen_at", "price", "price_per_sqm", "updated_at"))
    # Проверка карточки меняет доступность связей, но не дату наблюдения в поисковой выдаче.
    ListingSearchQuery.objects.filter(listing=listing).update(is_active=available)
    check = AvitoManualCheck.objects.create(
        listing=listing, author=author, action=action, checked_at=observed,
        old_price=old_price, new_price=listing.price, operation_id=operation_id,
    )
    if old_price != listing.price:
        PriceHistory.objects.create(listing=listing, price=listing.price, observed_at=observed)
        transaction.on_commit(lambda: notify_price_change(listing.pk, old_price, listing.price), robust=True)
    after = (listing.is_active, listing.publication_status, listing.price, listing.price_per_sqm)
    if before != after:
        ListingSnapshot.objects.create(listing=listing, observed_at=observed, data={
            **snapshot_data_from_listing(listing),
            "avito_manual_check": {"id": check.pk, "action": action, "author_id": author.pk},
        })
    return check, True
