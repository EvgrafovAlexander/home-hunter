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
