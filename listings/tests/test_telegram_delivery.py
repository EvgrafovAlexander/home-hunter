import pytest

from listings.models import Listing, TelegramListingAlert
from listings.services.telegram_delivery import (
    claim_listing_alerts,
    enqueue_new_listing_alert,
    telegram_listing_eligibility,
)


pytestmark = pytest.mark.django_db


def make_listing(**kwargs):
    values = dict(
        source="avito", external_id="delivery-1", url="https://example.test/delivery-1",
        title="2-комнатная квартира", price=10_000_000, rooms=2, area="60.00",
        district="Ленинский", address="Уфа, улица Тестовая, 1", is_visible=True,
    )
    values.update(kwargs)
    return Listing.objects.create(**values)


def test_new_listing_waits_for_first_geocoding_attempt(settings):
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    listing = make_listing()
    state, reason = telegram_listing_eligibility(listing)
    assert state == TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE
    assert "first geocoding" in reason

    alert = enqueue_new_listing_alert(listing.pk)
    assert alert.delivery_status == TelegramListingAlert.DeliveryStatus.AWAITING_GEOCODE
    assert claim_listing_alerts() == []


def test_unknown_geocode_result_is_publishable(settings):
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    listing = make_listing(geocode_status="not_found")
    alert = enqueue_new_listing_alert(listing.pk)
    assert alert.delivery_status == TelegramListingAlert.DeliveryStatus.PENDING
    assert claim_listing_alerts() == [alert.pk]
    assert TelegramListingAlert.objects.get(pk=alert.pk).delivery_status == TelegramListingAlert.DeliveryStatus.SENDING


def test_primary_unfinished_listing_is_skipped(settings):
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    listing = make_listing(title="ЖК Новый дом, квартира без отделки", geocode_status="not_found")
    alert = enqueue_new_listing_alert(listing.pk)
    assert alert.delivery_status == TelegramListingAlert.DeliveryStatus.SKIPPED
    assert alert.filter_reason == "primary/unfinished listing"


@pytest.mark.parametrize("missing_deadline", [False, True])
def test_expired_claim_recovered_and_stale_worker_cannot_finish(settings, missing_deadline):
    from datetime import timedelta
    from django.utils import timezone
    from listings.services.telegram_delivery import (
        mark_listing_alert_failed, mark_listing_alert_sent, renew_listing_alert_claim,
    )
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    alert = enqueue_new_listing_alert(make_listing(geocode_status="not_found").pk)
    assert claim_listing_alerts(with_attempts=True) == [(alert.pk, 1)]
    assert claim_listing_alerts(with_attempts=True) == []
    TelegramListingAlert.objects.filter(pk=alert.pk).update(
        locked_until=None if missing_deadline else timezone.now() - timedelta(seconds=1),
    )
    assert not renew_listing_alert_claim(alert.pk, 1)
    assert claim_listing_alerts(with_attempts=True) == [(alert.pk, 2)]
    assert not renew_listing_alert_claim(alert.pk, 1)
    assert not mark_listing_alert_failed(alert.pk, "old worker", attempt=1)
    assert not mark_listing_alert_sent(alert.pk, attempt=1, result={"message_id": 10}, message_text="old")
    assert mark_listing_alert_sent(alert.pk, attempt=2, result={"message_id": 11}, message_text="new")
    # Ошибка после сохранения успеха не должна возвращать отправленное в очередь.
    assert not mark_listing_alert_failed(alert.pk, "score refresh failed", attempt=2)
    assert claim_listing_alerts(with_attempts=True) == []
    alert.refresh_from_db()
    assert alert.message_id == 11
    assert alert.delivery_status == TelegramListingAlert.DeliveryStatus.SENT


def test_expired_claim_rechecks_visibility(settings):
    from datetime import timedelta
    from django.utils import timezone
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    listing = make_listing(geocode_status="not_found")
    alert = enqueue_new_listing_alert(listing.pk)
    assert claim_listing_alerts() == [alert.pk]
    listing.is_visible = False
    listing.save(update_fields=("is_visible",))
    TelegramListingAlert.objects.filter(pk=alert.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    assert claim_listing_alerts() == []
    alert.refresh_from_db()
    assert alert.delivery_status == TelegramListingAlert.DeliveryStatus.SKIPPED


def test_stale_attempt_does_not_send(settings, monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from listings.services.deal_alerts import notify_new_listing
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123"
    alert = enqueue_new_listing_alert(make_listing(geocode_status="not_found").pk)
    claim_listing_alerts()
    TelegramListingAlert.objects.filter(pk=alert.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    claim_listing_alerts()
    def unexpected_send(*args, **kwargs):
        pytest.fail("Устаревшая попытка не должна отправлять сообщение")
    monkeypatch.setattr("listings.services.deal_alerts._send_reviewable_listing", unexpected_send)
    assert not notify_new_listing(0, alert_id=alert.pk, attempt=1)
