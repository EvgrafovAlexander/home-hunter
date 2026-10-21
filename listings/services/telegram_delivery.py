"""Durable queue and eligibility checks for Telegram listing publications."""

import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.utils import timezone

from listings.models import GeoZone, Listing, TelegramListingAlert
from listings.services.personal_filters import zone_for_listing

MIN_TARGET_AREA = Decimal("55")
CLAIM_LEASE = timedelta(minutes=2)

_UNFINISHED_PATTERNS = (
    r"без\s+отделк",
    r"чернов(?:ая|ой)\s+отделк",
    r"предчист(?:овая|овой)\s+отделк",
    r"без\s+чистовой\s+отделк",
    r"отсутств(?:ует|ие)\s+отделк",
)
_NEW_BUILD_CONTEXT = re.compile(r"\b(?:жк|нов(?:ый|острой)|застрой|сдан|дом\s+20(?:2[4-9]|3\d))", re.I)


def _has_unfinished_primary_text(listing: Listing) -> bool:
    text = " ".join(filter(None, (listing.title, listing.description))).lower()
    if any(re.search(pattern, text, flags=re.I) for pattern in _UNFINISHED_PATTERNS):
        return True
    # Generic "без ремонта" is allowed in old secondary listings unless the
    # same card also looks like a new-build/developer offer.
    return "без ремонта" in text and bool(_NEW_BUILD_CONTEXT.search(text))


def _zone_tier_for_telegram(listing: Listing):
    username = getattr(settings, "TG_REVIEW_DJANGO_USERNAME", "")
    user = get_user_model().objects.filter(username=username).first() if username else None
    if not user:
        return None
    zones = list(GeoZone.objects.filter(user=user, is_active=True))
    if not zones:
        return None
    listing._personal_filter_user_id = user.id
    return zone_for_listing(listing, zones)


def telegram_listing_eligibility(listing: Listing):
    """Return ``(state, reason)`` for the Telegram listing publication gate."""
    if not settings.TG_LISTING_ALERTS_ENABLED or not getattr(settings, "TG_CHANNEL_ID", ""):
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "Telegram listing alerts disabled"
    if not listing.is_visible:
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "hidden location"
    if listing.area is None or Decimal(str(listing.area)) < MIN_TARGET_AREA:
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "area below 55 m²"
    if not (listing.address or "").strip():
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "address is missing"
    if listing.latitude is None or listing.longitude is None:
        if not listing.geocode_status:
            return TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE, "waiting for first geocoding attempt"
    if _zone_tier_for_telegram(listing) == GeoZone.Tier.D:
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "zone D: not considered"
    if _has_unfinished_primary_text(listing):
        return TelegramListingAlert.DeliveryStatus.SKIPPED, "primary/unfinished listing"
    return TelegramListingAlert.DeliveryStatus.PENDING, ""


def enqueue_new_listing_alert(listing_id: int):
    """Create or refresh the durable publication record without network I/O."""
    listing = Listing.objects.get(pk=listing_id)
    state, reason = telegram_listing_eligibility(listing)
    now = timezone.now()
    alert, created = TelegramListingAlert.objects.get_or_create(
        listing=listing,
        defaults={
            "delivery_status": state,
            "filter_reason": reason,
            "filter_checked_at": now,
            "next_attempt_at": now if state == TelegramListingAlert.DeliveryStatus.PENDING else None,
        },
    )
    if created or alert.delivery_status in {
        TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE,
        TelegramListingAlert.DeliveryStatus.FAILED,
    }:
        if alert.delivery_status == TelegramListingAlert.DeliveryStatus.SENT:
            return alert
        alert.delivery_status = state
        alert.filter_reason = reason
        alert.filter_checked_at = now
        if state == TelegramListingAlert.DeliveryStatus.PENDING:
            alert.next_attempt_at = now
        alert.save(update_fields=("delivery_status", "filter_reason", "filter_checked_at", "next_attempt_at"))
    return alert


@transaction.atomic
def claim_listing_alerts(limit: int = 10):
    """Claim due rows for one worker process and return their primary keys."""
    now = timezone.now()
    rows = list(
        TelegramListingAlert.objects.select_for_update(skip_locked=True).select_related("listing")
        .filter(delivery_status__in=(
            TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE,
            TelegramListingAlert.DeliveryStatus.PENDING,
            TelegramListingAlert.DeliveryStatus.FAILED,
        ))
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
        .order_by("pk")[:limit]
    )
    claimed = []
    for alert in rows:
        if alert.locked_until and alert.locked_until > now:
            continue
        state, reason = telegram_listing_eligibility(alert.listing)
        alert.filter_checked_at = now
        alert.filter_reason = reason
        if state == TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE:
            alert.delivery_status = state
            alert.next_attempt_at = now + timedelta(minutes=10)
            alert.save(update_fields=("delivery_status", "filter_reason", "filter_checked_at", "next_attempt_at"))
            continue
        if state == TelegramListingAlert.DeliveryStatus.SKIPPED:
            alert.delivery_status = state
            alert.next_attempt_at = None
            alert.locked_until = None
            alert.save(update_fields=("delivery_status", "filter_reason", "filter_checked_at", "next_attempt_at", "locked_until"))
            continue
        alert.delivery_status = TelegramListingAlert.DeliveryStatus.SENDING
        alert.delivery_attempts += 1
        alert.locked_until = now + CLAIM_LEASE
        alert.next_attempt_at = None
        alert.save(update_fields=("delivery_status", "delivery_attempts", "locked_until", "next_attempt_at", "filter_reason", "filter_checked_at"))
        claimed.append(alert.pk)
    return claimed


def mark_listing_alert_sent(alert_id: int, *, result: dict, message_text: str):
    TelegramListingAlert.objects.filter(pk=alert_id).update(
        delivery_status=TelegramListingAlert.DeliveryStatus.SENT,
        chat_id=str(settings.TG_CHANNEL_ID),
        message_id=result.get("message_id"),
        message_kind=result.get("_home_hunter_message_kind", "text"),
        message_text=message_text,
        sent_at=timezone.now(),
        locked_until=None,
        next_attempt_at=None,
        last_delivery_error="",
    )


def mark_listing_alert_failed(alert_id: int, error: str):
    alert = TelegramListingAlert.objects.get(pk=alert_id)
    delay = min(3600, 60 * (2 ** min(alert.delivery_attempts, 5)))
    alert.delivery_status = TelegramListingAlert.DeliveryStatus.FAILED
    alert.last_delivery_error = error[:2000]
    alert.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    alert.locked_until = None
    alert.save(update_fields=("delivery_status", "last_delivery_error", "next_attempt_at", "locked_until"))
