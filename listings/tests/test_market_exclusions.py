"""Единая выборка рынка, ручные пометки, перенос оценок и запрет отправок."""

from copy import deepcopy
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from listings.models import Listing, ListingReview, MarketDailySnapshot, MarketExclusion, TelegramListingAlert
from listings.services.market_exclusions import exclude_from_market, restore_to_market, unfinished_review_candidates
from listings.services.market import annotate_market_position, market_position_details
from listings.services.market_trends import market_trends_context
from listings.services.market_export import build_market_xlsx
from listings.services.telegram_delivery import enqueue_new_listing_alert, claim_listing_alerts
from listings.services.deal_alerts import notify_new_listing
from listings.services.price_alerts import notify_price_change

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    return get_user_model().objects.create_user(username="market-editor", is_staff=True)


def flat(external_id="a", price=6000000):
    return Listing.objects.create(source="cian", external_id=external_id, title=f"Квартира {external_id}",
        url=f"https://ufa.cian.ru/sale/flat/{external_id}/", address="Уфа, Ленина, 10", district="Ленинский",
        microdistrict="Центр", area=60, rooms=2, floor=4, floors_total=9, price=price,
        price_per_sqm=price//60, is_visible=True, is_active=True, geocode_status="not_found")


def test_idempotent_permission_restore_and_history(staff):
    item = flat()
    ordinary = get_user_model().objects.create_user(username="ordinary")
    with pytest.raises(PermissionDenied):
        exclude_from_market(ordinary, item.pk, "unfinished")
    first, created = exclude_from_market(staff, item.pk, "unfinished", "Причина")
    assert created
    second, created = exclude_from_market(staff, item.pk, "other", "Не менять")
    assert not created and first.pk == second.pk
    assert second.reason == "unfinished"
    item.price = 5000000
    item.save()
    assert MarketExclusion.objects.filter(listing=item, is_active=True).exists()
    restore_to_market(staff, item.pk, first.pk)
    first.refresh_from_db()
    assert not first.is_active and first.restored_by == staff and first.restored_at
    assert Listing.objects.filter(pk=item.pk).exists()
    new, created = exclude_from_market(staff, item.pk, "bad_data")
    assert created and new.pk != first.pk


def test_comparisons_exclude_analogs_and_target(staff):
    target = flat("target")
    for i in range(5):
        flat(str(i))
    bad = flat("bad", 600000)
    exclude_from_market(staff, bad.pk, "unfinished")
    annotate_market_position([target, bad])
    assert target.market_samples == 5 and target.market_delta_pct == 0
    assert bad.market_delta_pct is None and bad.market_excluded
    assert market_position_details(bad) is None
    assert market_position_details(target)[2:] == (5, 100000)


def test_feed_dashboard_staff_controls_and_full_mode(client, staff):
    good, bad = flat("good"), flat("bad", 600000)
    client.force_login(staff)
    endpoint = reverse("market_exclusion_edit", args=[bad.pk])
    assert client.get(endpoint).status_code == 200
    assert not MarketExclusion.objects.exists()
    assert client.post(endpoint, {"action": "exclude", "reason": "unfinished"}).status_code == 302
    feed = client.get(reverse("listing_feed"))
    assert bad.pk not in [x.pk for x in feed.context["listings"]]
    assert good.pk in [x.pk for x in feed.context["listings"]]
    assert client.get(reverse("listing_detail", args=[bad.pk])).status_code == 200
    response = client.get(reverse("dashboard"))
    assert response.context["total"] == 1 and response.context["median_sqm_price"] == 100000
    assert client.get(reverse("dashboard"), {"include_excluded": "1"}).context["total"] == 2
    ordinary = get_user_model().objects.create_user(username="other")
    client.force_login(ordinary)
    assert client.post(endpoint, {"action": "restore", "exclusion_id": MarketExclusion.objects.get().pk}).status_code == 302
    assert MarketExclusion.objects.get().is_active
    client.force_login(staff)
    assert client.post(endpoint, {"action": "restore", "exclusion_id": MarketExclusion.objects.get().pk}).status_code == 302
    assert client.get(reverse("dashboard")).context["total"] == 2


def test_history_applies_same_exclusions_on_both_dates_without_mutating_snapshots(staff):
    good, bad = flat("good"), flat("bad")
    now = timezone.now()
    rows = [{"id": obj.pk, "price": price, "area": 60, "rooms": 2, "district": "Ленинский",
             "microdistrict": "Центр", "active": True, "first_seen_at": (now-timedelta(days=30)).isoformat()}
            for obj, price in ((good, 6000000), (bad, 600000))]
    for offset in range(8):
        stamp = now-timedelta(days=offset)
        MarketDailySnapshot.objects.create(day=stamp.date(), captured_at=stamp, source="cian", scope_hash="same",
            quality={"search_count":1, "successful_searches":1}, listings=deepcopy(rows))
    before = list(MarketDailySnapshot.objects.values_list("listings", flat=True))
    exclusion, _ = exclude_from_market(staff, bad.pk, "unfinished")
    filters = {"source":"cian", "weeks":12}
    context = market_trends_context(filters, today=now.date())
    point = context["points"][-1]
    assert point["median"] == 100000 and point["active"] == 1
    assert point["removed"] == 0 and point["matched_count"] == 1
    assert context["excluded_count"] == 1
    assert market_trends_context({**filters, "include_excluded":True}, today=now.date())["points"][-1]["active"] == 2
    assert list(MarketDailySnapshot.objects.values_list("listings", flat=True)) == before
    restore_to_market(staff, bad.pk, exclusion.pk)
    assert market_trends_context(filters, today=now.date())["points"][-1]["active"] == 2


def test_export_keeps_raw_reason_but_cleans_market_sheets(staff):
    for i in range(5):
        flat(str(i))
    bad = flat("bad", 600000)
    exclude_from_market(staff, bad.pk, "unfinished")
    with ZipFile(BytesIO(build_market_xlsx(staff))) as archive:
        raw = archive.read('xl/worksheets/sheet3.xml').decode()
        assert 'Исключено из рынка' in raw and 'Без отделки' in raw and bad.url in raw
        root = ET.fromstring(archive.read('xl/worksheets/sheet7.xml'))
        ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        row = root.findall('s:sheetData/s:row', ns)[1]
        cells = row.findall('s:c', ns)
        assert cells[2].find('s:v', ns).text == '5'
        assert cells[8].find('s:v', ns).text == '100000'


def test_review_backfill_is_precise_idempotent_and_respects_restore(staff):
    good = flat("unfinished")
    ListingReview.objects.create(listing=good, author=staff, rating=1, decision="reject", deal_breaker=" Отсутствует  отделка. ")
    for i, (rating, text) in enumerate([(1,"Плохой район"), (2,"Отсутствует отделка"), (1,"Не отсутствует отделка"), (1,"Без ремонта")]):
        ListingReview.objects.create(listing=flat(str(i)), author=staff, rating=rating, decision="reject", comment=text)
    assert [x.listing_id for x in unfinished_review_candidates(staff.pk)] == [good.pk]
    call_command('exclude_unfinished_reviews', author_id=staff.pk, stdout=StringIO())
    assert not MarketExclusion.objects.exists()
    for _ in range(2):
        call_command('exclude_unfinished_reviews', author_id=staff.pk, actor_id=staff.pk, apply=True, stdout=StringIO())
    exclusion = MarketExclusion.objects.get()
    restore_to_market(staff, good.pk, exclusion.pk)
    call_command('exclude_unfinished_reviews', author_id=staff.pk, actor_id=staff.pk, apply=True, stdout=StringIO())
    assert not MarketExclusion.objects.filter(is_active=True).exists()
    assert ListingReview.objects.count() == 5


def test_telegram_pending_and_claimed_messages_and_price_alerts_are_blocked(staff, settings):
    settings.TG_LISTING_ALERTS_ENABLED = True
    settings.TG_PRICE_ALERTS_ENABLED = True
    settings.TG_CHANNEL_ID = '-100123'
    pending = flat("pending")
    alert = enqueue_new_listing_alert(pending.pk)
    exclude_from_market(staff, pending.pk, "unfinished")
    alert.refresh_from_db()
    assert alert.delivery_status == 'skipped'
    assert claim_listing_alerts() == []
    sending = flat("sending")
    alert = enqueue_new_listing_alert(sending.pk)
    claims = claim_listing_alerts(1, with_attempts=True)
    assert claims == [(alert.pk, 1)]
    exclude_from_market(staff, sending.pk, "unfinished")
    with patch('listings.services.deal_alerts._send_reviewable_listing') as send:
        assert not notify_new_listing(sending.pk, alert_id=alert.pk, attempt=1)
        send.assert_not_called()
    alert.refresh_from_db()
    assert alert.delivery_status == 'skipped'
    with patch('listings.services.price_alerts.send_telegram_message') as send:
        assert not notify_price_change(sending.pk, 6000000, 5000000)
        send.assert_not_called()


def test_ingestion_does_not_remove_exclusion(staff):
    from decimal import Decimal
    from collectors.base import NormalizedListing
    from listings.models import SearchQuery
    from listings.services.persistence import process_listing
    item = flat()
    exclude_from_market(staff, item.pk, 'unfinished')
    search = SearchQuery.objects.create(name='Поиск', source='cian', url='https://ufa.cian.ru/cat.php')
    dto = NormalizedListing(source='cian', external_id=item.external_id, url=item.url,
                            title='Обновлённое объявление', price=5000000, area=Decimal('60'), rooms=2)
    process_listing(search, dto, timezone.now())
    item.refresh_from_db()
    assert item.title == 'Обновлённое объявление'
    assert MarketExclusion.objects.filter(listing=item, is_active=True).exists()


@pytest.mark.parametrize('available', [True, False])
def test_avito_checkbox_is_independent_of_availability(client, staff, available):
    item = flat()
    item.source = 'avito'
    item.is_active = available
    item.publication_status = 'published' if available else 'unavailable'
    item.last_seen_at = timezone.now()-timedelta(days=20)
    item.save()
    original = (item.is_active, item.publication_status, item.last_seen_at, item.price, item.updated_at)
    client.force_login(staff)
    return_to = reverse('avito_manual_checks') + ('?tab=queue&days=14&page=1' if available else '?tab=unavailable&days=14&page=1')
    html = client.get(return_to).content.decode()
    assert f'id="market-excluded-{item.pk}"' in html
    endpoint = reverse('market_exclusion_edit', args=[item.pk])
    response = client.post(endpoint, {'action':'set_excluded','excluded':'1','return_to':return_to})
    assert response.status_code == 302 and response.url == return_to
    exclusion = MarketExclusion.objects.get(listing=item,is_active=True)
    assert exclusion.reason == 'unfinished'
    assert 'checked' in client.get(return_to).content.decode().split(f'id="market-excluded-{item.pk}"')[1].split('>')[0]
    assert client.post(endpoint, {'action':'set_excluded','exclusion_id':exclusion.pk,'return_to':return_to}).url == return_to
    item.refresh_from_db()
    assert (item.is_active, item.publication_status, item.last_seen_at, item.price, item.updated_at) == original
    assert not MarketExclusion.objects.filter(listing=item,is_active=True).exists()
    response = client.post(endpoint, {'action':'set_excluded','excluded':'1','return_to':'//evil.test/avito-checks/'})
    assert response.url == endpoint
