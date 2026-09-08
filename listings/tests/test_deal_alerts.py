from unittest.mock import patch

import pytest

from listings.models import DealAlert, Listing
from listings.services.deal_alerts import notify_new_deal


pytestmark = pytest.mark.django_db


def test_notifies_once_for_a_new_listing_below_local_market(settings):
    settings.TG_DEAL_ALERTS_ENABLED = True
    settings.TG_DEAL_MIN_DISCOUNT_PERCENT = 7
    for index in range(5):
        Listing.objects.create(
            source="cian", external_id=f"comparison-{index}", url="https://example.test/comparison",
            title="Сравнение", price=7_500_000, price_per_sqm=150_000, rooms=2, area="50.00",
            district="Кировский", microdistrict="Центр",
        )
    deal = Listing.objects.create(
        source="avito", external_id="deal", url="https://example.test/deal", title="Выгодная квартира",
        price=6_250_000, price_per_sqm=125_000, rooms=2, area="50.00",
        district="Кировский", microdistrict="Центр",
    )
    with patch("listings.services.deal_alerts.send_telegram_message", return_value=True) as send:
        assert notify_new_deal(deal.pk)
        assert not notify_new_deal(deal.pk)
    assert "На 17% ниже рынка" in send.call_args.args[0]
    assert DealAlert.objects.get().listing == deal


def test_does_not_notify_when_microdistrict_has_too_few_analogues(settings):
    settings.TG_DEAL_ALERTS_ENABLED = True
    deal = Listing.objects.create(
        source="avito", external_id="zaton-deal", url="https://example.test/zaton", title="Затон",
        price=4_000_000, price_per_sqm=80_000, rooms=2, area="50.00",
        district="Ленинский", microdistrict="Затон", is_visible=True,
    )
    for index in range(12):
        Listing.objects.create(
            source="cian", external_id=f"center-{index}", url="https://example.test/center", title="Центр",
            price=8_000_000, price_per_sqm=160_000, rooms=2, area="50.00",
            district="Ленинский", microdistrict="Центр",
        )
    with patch("listings.services.deal_alerts.send_telegram_message") as send:
        assert not notify_new_deal(deal.pk)
    send.assert_not_called()


def test_uses_photo_when_listing_has_one(settings):
    settings.TG_DEAL_ALERTS_ENABLED = True
    for index in range(5):
        Listing.objects.create(
            source="cian", external_id=f"photo-comparison-{index}", url="https://example.test/comparison",
            title="Сравнение", price=7_500_000, price_per_sqm=150_000, rooms=2, area="50.00",
            district="Кировский", microdistrict="Центр",
        )
    deal = Listing.objects.create(
        source="avito", external_id="photo-deal", url="https://example.test/deal", title="Выгодная квартира",
        price=6_250_000, price_per_sqm=125_000, rooms=2, area="50.00",
        district="Кировский", microdistrict="Центр", image_url="https://example.test/photo.jpg",
    )
    with patch("listings.services.deal_alerts.send_telegram_photo", return_value=True) as photo, \
            patch("listings.services.deal_alerts.send_telegram_message") as message:
        assert notify_new_deal(deal.pk)
    photo.assert_called_once()
    message.assert_not_called()
