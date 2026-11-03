"""Проверки ручного workflow Avito, истории и взаимодействия с импортом."""

from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from listings.models import AvitoManualCheck, Listing, ListingSearchQuery, ListingSnapshot, PriceHistory, SearchQuery
from listings.services.avito_manual_checks import (
    StaleAvitoCheck, avito_check_queue, parse_manual_price, save_avito_manual_check,
)
from listings.services.avito_xlsx_import import import_avito_xlsx

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return get_user_model().objects.create_user(username="checker", is_staff=True)


@pytest.fixture
def listing():
    return Listing.objects.create(
        source="avito", external_id="123456", url="https://www.avito.ru/ufa/kvartiry/test_123456",
        title="2-к. квартира, 60 м², 3/9 эт.", area=Decimal("60"), price=6_000_000,
        price_per_sqm=100_000, last_seen_at=timezone.now() - timedelta(days=15),
    )


def save(listing, staff, action="available", **kwargs):
    return save_avito_manual_check(
        listing_id=listing.pk, author=staff, action=action,
        operation_id=kwargs.pop("operation_id", uuid4()),
        expected_version=kwargs.pop("expected_version", listing.updated_at.isoformat()), **kwargs,
    )


def token(listing, staff):
    return signing.dumps({"listing": listing.pk, "user": staff.pk,
                          "version": listing.updated_at.isoformat(), "operation": str(uuid4())},
                         salt="avito-manual-check")


def test_queue_age_source_status_and_order(listing):
    older = Listing.objects.create(source="avito", external_id="older", last_seen_at=timezone.now()-timedelta(days=40))
    Listing.objects.create(source="cian", external_id="cian", last_seen_at=older.last_seen_at)
    Listing.objects.create(source="avito", external_id="new")
    unavailable = Listing.objects.create(source="avito", external_id="off", is_active=False)
    assert list(avito_check_queue()) == [older, listing]
    assert list(avito_check_queue(days=30)) == [older]
    assert list(avito_check_queue(unavailable=True)) == [unavailable]


def test_available_records_actor_and_time_without_price_history(listing, staff):
    check, created = save(listing, staff)
    listing.refresh_from_db()
    assert created and check.author == staff
    assert listing.last_seen_at == check.checked_at
    assert listing.is_active and listing.publication_status == "published"
    assert not avito_check_queue().exists()
    assert not PriceHistory.objects.exists()
    assert not ListingSnapshot.objects.exists()
    assert avito_check_queue(days=3).count() == 0


def test_unavailable_and_restore_preserve_data(listing, staff):
    search = SearchQuery.objects.create(source="avito", name="test", url="https://www.avito.ru/ufa/kvartiry")
    relation = ListingSearchQuery.objects.create(listing=listing, search_query=search)
    old_seen = listing.last_seen_at
    save(listing, staff, "unavailable")
    listing.refresh_from_db(); relation.refresh_from_db()
    assert not listing.is_active and listing.publication_status == "unavailable"
    assert listing.last_seen_at == old_seen and not relation.is_active
    assert ListingSnapshot.objects.get().data["is_active"] is False
    assert avito_check_queue(unavailable=True).get().last_manual_check_at is not None
    save(listing, staff)
    listing.refresh_from_db(); relation.refresh_from_db()
    assert listing.is_active and relation.is_active and listing.price == 6_000_000
    assert AvitoManualCheck.objects.count() == ListingSnapshot.objects.count() == 2


@pytest.mark.parametrize("area,expected", [(Decimal("60"), 110_000), (Decimal("0"), None), (None, None)])
def test_price_recalculation_history_and_after_commit(listing, staff, area, expected, django_capture_on_commit_callbacks):
    listing.area = area
    listing.save()
    with patch("listings.services.avito_manual_checks.notify_price_change") as notify:
        with django_capture_on_commit_callbacks(execute=True):
            save(listing, staff, "price", price="6 600 000")
            notify.assert_not_called()
        notify.assert_called_once_with(listing.pk, 6_000_000, 6_600_000)
    listing.refresh_from_db()
    assert listing.price == 6_600_000 and listing.price_per_sqm == expected
    assert PriceHistory.objects.get().price == 6_600_000
    snapshot = ListingSnapshot.objects.get().data
    assert snapshot["price_per_sqm"] == expected and snapshot["price"] == 6_600_000
    assert not avito_check_queue().exists()


def test_same_price_confirms_without_fake_history(listing, staff):
    save(listing, staff, "price", price="6000000")
    assert not PriceHistory.objects.exists() and not ListingSnapshot.objects.exists()
    assert AvitoManualCheck.objects.count() == 1
    assert not avito_check_queue().exists()


def test_repeat_and_stale_operations(listing, staff):
    operation = uuid4()
    check, _ = save(listing, staff, operation_id=operation)
    repeated, created = save(listing, staff, operation_id=operation)
    assert repeated.pk == check.pk and not created
    with pytest.raises(StaleAvitoCheck):
        save(listing, staff, "unavailable")
    assert AvitoManualCheck.objects.count() == 1
    # Даже после последующего изменения повтор старой операции не меняет объявление.
    listing.refresh_from_db()
    save(listing, staff, "unavailable")
    save(listing, staff, operation_id=operation)
    listing.refresh_from_db()
    assert not listing.is_active


@pytest.mark.parametrize("value", ["", "0", "-1", "1.2", "NaN", "1e6", "1000000000001"])
def test_price_validation(value):
    with pytest.raises(ValueError):
        parse_manual_price(value)


def test_transaction_rolls_back_on_history_failure(listing, staff):
    with patch("listings.services.avito_manual_checks.PriceHistory.objects.create", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            save(listing, staff, "price", price="7000000")
    listing.refresh_from_db()
    assert listing.price == 6_000_000 and not AvitoManualCheck.objects.exists()


def test_import_restores_and_invalidates_open_form(listing, staff, settings):
    settings.TG_PRICE_ALERTS_ENABLED = False
    save(listing, staff, "unavailable")
    listing.refresh_from_db()
    search = SearchQuery.objects.create(source="avito", name="test", url="https://www.avito.ru/ufa/kvartiry")
    book = Workbook(); sheet = book.active; sheet.title = "Items"
    sheet.append(["itemId", "title", "url", "sourcePageUrl", "priceValue"])
    sheet.append([listing.external_id, listing.title, listing.url, search.url, 6_000_000])
    stream = BytesIO(); book.save(stream); stream.seek(0)
    import_avito_xlsx(stream, search)
    with pytest.raises(StaleAvitoCheck):
        save(listing, staff, "unavailable")
    listing.refresh_from_db()
    assert listing.is_active and listing.publication_status == "published"
    assert listing.price_per_sqm == 100_000
    assert AvitoManualCheck.objects.count() == 1
    assert ListingSnapshot.objects.order_by("-id").first().data["is_active"] is True
    assert not avito_check_queue().exists()


def test_staff_page_actions_and_more_menu(client, listing, staff):
    url = reverse("avito_manual_checks")
    assert client.get(url).status_code == 302
    staff.is_staff = False; staff.save(); client.force_login(staff)
    assert client.get(url).status_code == 302
    staff.is_staff = True; staff.save()
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "Обновить цену" in html and 'value="6000000"' in html
    menu = html.split('<div class="nav-menu">')[1].split('</div>')[0]
    assert f'href="{url}"' in menu
    assert client.post(url, {"action": "unavailable", "token": "fake"}).status_code == 400
    assert client.post(url, {"action": "price", "token": token(listing, staff), "price": "-10"}).status_code == 400
    assert client.post(url, {"action": "available", "token": token(listing, staff)}).status_code == 302
    assert client.post(url, {"action": "unavailable", "token": token(listing, staff)}).status_code == 409
    assert client.get(url).context["page"].paginator.count == 0


def test_csrf_and_token_user_binding(client, listing, staff):
    from django.test import Client
    url = reverse("avito_manual_checks")
    guarded = Client(enforce_csrf_checks=True); guarded.force_login(staff)
    assert guarded.post(url, {"action": "available", "token": token(listing, staff)}).status_code == 403
    other = get_user_model().objects.create_user(username="other", is_staff=True)
    client.force_login(other)
    assert client.post(url, {"action": "available", "token": token(listing, staff)}).status_code == 400
    assert not AvitoManualCheck.objects.exists()


def test_same_price_repairs_stale_sqm_and_restores_unavailable(listing, staff):
    listing.price_per_sqm = 1
    listing.is_active = False
    listing.publication_status = "unavailable"
    listing.save()
    save(listing, staff, "price", price="6\u00a0000\u00a0000")
    listing.refresh_from_db()
    assert listing.price_per_sqm == 100_000 and listing.is_active
    assert listing.publication_status == "published"
    assert not PriceHistory.objects.exists()
    assert ListingSnapshot.objects.get().data["price_per_sqm"] == 100_000


def test_failed_notification_does_not_undo_saved_price(listing, staff, django_capture_on_commit_callbacks):
    with patch("listings.services.avito_manual_checks.notify_price_change", side_effect=RuntimeError("Ошибка доставки")):
        with django_capture_on_commit_callbacks(execute=True):
            save(listing, staff, "price", price="7000000")
    listing.refresh_from_db()
    assert listing.price == 7_000_000
    assert AvitoManualCheck.objects.count() == PriceHistory.objects.count() == 1
