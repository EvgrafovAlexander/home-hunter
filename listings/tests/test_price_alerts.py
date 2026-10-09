from unittest.mock import patch

import pytest

from listings.models import Listing, TelegramListingAlert, TelegramPriceAlert
from listings.services.price_alerts import notify_price_change


pytestmark = pytest.mark.django_db


def make_listing(**kwargs):
    values = dict(
        source="cian", external_id="price-1", url="https://example.test/price-1",
        title="Квартира", price=10_000_000, rooms=2, area="60.00",
        district="Ленинский", is_visible=True,
    )
    values.update(kwargs)
    return Listing.objects.create(**values)


def test_price_alert_ignores_small_listing(settings):
    settings.TG_PRICE_ALERTS_ENABLED = True
    listing = make_listing(area="45.00")
    with patch("listings.services.price_alerts.send_telegram_message") as send:
        assert not notify_price_change(listing.pk, 10_500_000, 9_990_000)
    send.assert_not_called()
    assert not TelegramPriceAlert.objects.exists()


def test_price_alert_updates_original_post_and_links_to_it(settings):
    settings.TG_PRICE_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = "-100123456"
    listing = make_listing(price=10_500_000, price_per_sqm=175_000)
    alert = TelegramListingAlert.objects.create(
        listing=listing, chat_id=settings.TG_CHANNEL_ID, message_id=42,
        message_kind="text", message_text="Квартира\n\n10 500 000 ₽ · 175 000 ₽/м²\n\n📍 Ленинский",
    )
    with patch("listings.services.price_alerts.edit_telegram_message", return_value=True) as edit, \
            patch("listings.services.price_alerts.send_telegram_message", return_value=True) as send:
        assert notify_price_change(listing.pk, 10_500_000, 9_990_000)
    edit.assert_called_once()
    updated_text = edit.call_args.kwargs["text"]
    assert "9 990 000 ₽" in updated_text
    assert "Цена снижена" in updated_text
    assert "https://t.me/c/123456/42" in send.call_args.args[0]
    assert TelegramPriceAlert.objects.filter(listing=listing).exists()
