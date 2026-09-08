from datetime import datetime, timedelta, timezone as datetime_timezone

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from listings.models import Listing, PriceHistory, Scan, SearchQuery, SourcePollingControl
from listings.views import add_market_position, next_poll_runs


class ListingViewsTests(TestCase):
    def setUp(self):
        self.user = self._create_user()
        now = timezone.now()
        self.match = Listing.objects.create(
            source="cian", external_id="match", url="https://example.test/match", title="Двушка",
            price=6_500_000, price_per_sqm=125_000, rooms=2, area="52.00", floor=4,
            floors_total=9, district="Кировский", first_seen_at=now - timedelta(hours=2),
        )
        Listing.objects.create(
            source="avito", external_id="other", url="https://example.test/other", title="Трешка",
            price=9_000_000, price_per_sqm=150_000, rooms=3, area="60.00", floor=8,
            district="Советский", is_active=False,
        )
        self.hidden = Listing.objects.create(
            source="cian", external_id="hidden", url="https://example.test/hidden", title="В Дёмском",
            address="Дагестанская улица, 33", district="Дёмский", is_visible=False,
        )
        self.kalininsky = Listing.objects.create(
            source="cian", external_id="kalininsky", url="https://example.test/kalininsky", title="В Калининском",
            address="улица Ферина, 4", district="Калининский", is_visible=False,
        )

    def _create_user(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user("alex", password="secret")

    def test_feed_requires_login(self):
        response = self.client.get(reverse("listing_feed"))
        self.assertRedirects(response, f"/accounts/login/?next={reverse('listing_feed')}")

    def test_feed_shows_active_listings_and_applies_filters(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_feed"), {"rooms": 2, "price_max": 7_000_000})
        self.assertContains(response, "Двушка")
        self.assertNotContains(response, "Трешка")
        self.assertEqual(response.context["result_count"], 1)
        self.assertContains(response, reverse("listing_detail", args=[self.match.pk]))
        self.assertContains(response, 'href="https://example.test/match"')

    def test_listing_detail_shows_price_history_and_source_link(self):
        PriceHistory.objects.create(listing=self.match, price=6_700_000, observed_at=timezone.now() - timedelta(days=1))
        PriceHistory.objects.create(listing=self.match, price=6_500_000, observed_at=timezone.now())
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_detail", args=[self.match.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "История цены")
        self.assertContains(response, "6 700 000")
        self.assertContains(response, "Открыть на сайте")

    def test_listing_detail_links_to_its_point_on_map(self):
        self.match.latitude, self.match.longitude = "54.738800", "55.972100"
        self.match.save(update_fields=["latitude", "longitude"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_detail", args=[self.match.pk]))
        map_url = f'{reverse("listing_map")}?listing={self.match.pk}'
        self.assertContains(response, map_url)

        response = self.client.get(map_url)
        self.assertEqual(response.context["selected_listing_id"], self.match.pk)
        self.assertContains(response, f"const selectedListingId='{self.match.pk}'")

    def test_feed_shows_market_position_with_enough_comparables(self):
        for index in range(5):
            Listing.objects.create(
                source="avito", external_id=f"comparison-{index}", url="https://example.test/comparison",
                title="Сравнение", price=7_500_000, price_per_sqm=150_000, rooms=2, area="52.00",
                district="Кировский",
            )
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_feed"))
        self.assertContains(response, "На 17% ниже рынка")
        self.assertContains(response, "похожим квартирам, 5 аналогов")

    def test_shortlist_shows_recent_below_market_listings(self):
        candidate = Listing.objects.create(
            source="avito", external_id="best", url="https://example.test/best", title="Лучший вариант",
            price=5_000_000, price_per_sqm=100_000, rooms=2, area="52.00", district="Кировский",
        )
        for index in range(5):
            Listing.objects.create(
                source="cian", external_id=f"best-comparison-{index}", url="https://example.test/comparison",
                title="Аналог", price=7_500_000, price_per_sqm=150_000, rooms=2, area="52.00",
                district="Кировский",
            )
        self.client.force_login(self.user)
        response = self.client.get(reverse("shortlist"))
        self.assertContains(response, "Лучшие варианты")
        self.assertContains(response, "Лучший вариант")
        self.assertIn(candidate, response.context["listings"])

    def test_listing_detail_shows_similar_local_listings(self):
        similar = Listing.objects.create(
            source="avito", external_id="similar", url="https://example.test/similar", title="Похожая",
            price=6_600_000, price_per_sqm=126_000, rooms=2, area="55.00", floor=5,
            district="Кировский",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_detail", args=[self.match.pk]))
        self.assertContains(response, "Похожие квартиры")
        self.assertIn(similar, response.context["similar_listings"])

    def test_market_position_does_not_mix_microdistricts_inside_one_district(self):
        zaton = Listing.objects.create(
            source="avito", external_id="zaton", url="https://example.test/zaton", title="Затон",
            price=4_000_000, price_per_sqm=80_000, rooms=2, area="50.00",
            district="Ленинский", microdistrict="Затон", is_visible=False,
        )
        for index in range(12):
            Listing.objects.create(
                source="cian", external_id=f"center-{index}", url="https://example.test/center",
                title="Центр", price=8_000_000, price_per_sqm=160_000, rooms=2, area="50.00",
                district="Ленинский", microdistrict="Центр",
            )
        add_market_position([zaton])
        self.assertEqual(zaton.market_label, "")

    def test_feed_excludes_hidden_listing_and_separate_feed_shows_it(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_feed"))
        self.assertNotContains(response, "В Дёмском")
        self.assertNotContains(response, "В Калининском")
        response = self.client.get(reverse("hidden_listing_feed"))
        self.assertContains(response, "В Дёмском")
        self.assertContains(response, "В Калининском")

    def test_dashboard_uses_filtered_population(self):
        PriceHistory.objects.create(listing=self.match, price=6_700_000,
                                    observed_at=timezone.now() - timedelta(days=2))
        PriceHistory.objects.create(listing=self.match, price=6_500_000,
                                    observed_at=timezone.now() - timedelta(days=1))
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"), {"district": "Кировский", "stats_days": 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["median_price"], 6_500_000)
        self.assertEqual(response.context["period_days"], 7)
        self.assertEqual(response.context["price_drop_count"], 1)
        self.assertContains(response, "Самые заметные снижения")

    def test_scan_statistics_shows_three_latest_scans_per_source(self):
        search = SearchQuery.objects.create(name="Avito search", source="avito", url="https://example.test")
        for index in range(4):
            Scan.objects.create(search_query=search, source="avito", mode="fast", status="success",
                                items_seen=index, new_items=index)
        self.client.force_login(self.user)
        response = self.client.get(reverse("scan_statistics"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["sources"][0]["scans"]), 3)
        self.assertContains(response, "Avito search", count=3)

    def test_scan_statistics_highlights_consecutive_failures(self):
        search = SearchQuery.objects.create(name="CIAN search", source="cian", url="https://example.test/cian")
        for _ in range(3):
            Scan.objects.create(search_query=search, source="cian", mode="fast", status="failed")
        self.client.force_login(self.user)
        response = self.client.get(reverse("scan_statistics"))
        cian = next(source for source in response.context["sources"] if source["label"] == "CIAN")
        self.assertEqual(cian["health"], "error")
        self.assertContains(response, "3 ошибки подряд")

    def test_scan_statistics_can_disable_and_enable_source(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("scan_statistics"), {"source": "cian", "mode": "fast", "enabled": "0"})
        self.assertRedirects(response, reverse("scan_statistics"))
        self.assertFalse(SourcePollingControl.objects.get(source="cian", mode="fast").enabled)

        response = self.client.post(reverse("scan_statistics"), {"source": "cian", "mode": "fast", "enabled": "1"})
        self.assertRedirects(response, reverse("scan_statistics"))
        self.assertTrue(SourcePollingControl.objects.get(source="cian", mode="fast").enabled)

    def test_scan_statistics_can_queue_manual_domclick_job(self):
        SearchQuery.objects.create(name="Домклик", source="domclick", url="https://ufa.domclick.ru/search")
        self.client.force_login(self.user)
        response = self.client.post(reverse("scan_statistics"), {"action": "manual_domclick"})
        self.assertRedirects(response, reverse("scan_statistics"))
        from listings.models import ManualDomclickJob
        self.assertEqual(ManualDomclickJob.objects.count(), 1)

    def test_scan_statistics_does_not_allow_enabling_avito_full(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("scan_statistics"), {"source": "avito", "mode": "full", "enabled": "1"})
        self.assertRedirects(response, reverse("scan_statistics"))
        self.assertFalse(SourcePollingControl.objects.filter(source="avito", mode="full", enabled=True).exists())

    def test_data_quality_lists_missing_fields_and_saves_manual_location(self):
        incomplete = Listing.objects.create(
            source="avito", external_id="incomplete", url="https://example.test/incomplete",
            title="Без локации", address="ул. Ленина, 10", latitude="54.7", longitude="55.9",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("data_quality"))
        self.assertContains(response, "Без локации")
        self.assertContains(response, "Нет района")
        self.assertContains(response, "Нет микрорайона")
        self.assertContains(response, 'href="https://example.test/incomplete"')

        from listings.models import District, Microdistrict
        district = District.objects.get(name="Кировский")
        microdistrict = Microdistrict.objects.get(district=district, name="Южный")
        response = self.client.post(reverse("data_quality") + "?issue=district", {
            "listing_id": incomplete.pk, "issue": "district", "address": "ул. Ленина, 10",
            "district_id": district.pk, "microdistrict_id": microdistrict.pk,
        })
        self.assertRedirects(response, reverse("data_quality") + "?issue=district")
        incomplete.refresh_from_db()
        self.assertEqual((incomplete.address, incomplete.district, incomplete.microdistrict),
                         ("ул. Ленина, 10", "Кировский", "Южный"))
        self.assertEqual(incomplete.district_override, "Кировский")
        self.assertEqual(incomplete.district_ref, district)
        self.assertEqual(incomplete.microdistrict_ref, microdistrict)
        self.assertEqual(incomplete.location_source, "manual")
        self.assertIsNone(incomplete.latitude)

    def test_location_directory_adds_microdistrict_and_street_rule(self):
        from listings.models import District, Microdistrict, StreetAssignment
        district = District.objects.get(name="Кировский")
        self.client.force_login(self.user)
        response = self.client.post(reverse("location_directory"), {
            "action": "microdistrict", "district_id": district.pk, "name": "Тестовый",
        })
        self.assertRedirects(response, reverse("location_directory"))
        microdistrict = Microdistrict.objects.get(district=district, name="Тестовый")
        response = self.client.post(reverse("location_directory"), {
            "action": "street", "street": "Тестовая улица", "house_from": "1", "house_to": "99",
            "parity": "any", "district_id": district.pk, "microdistrict_id": microdistrict.pk,
        })
        self.assertRedirects(response, reverse("location_directory"))
        self.assertTrue(StreetAssignment.objects.filter(street="Тестовая улица", microdistrict=microdistrict).exists())

    def test_data_quality_rejects_microdistrict_from_another_district(self):
        from listings.models import District, Microdistrict
        item = Listing.objects.create(source="avito", external_id="mismatch", url="https://example.test/mismatch",
                                      title="Проверка", address="ул. Ленина, 10")
        district = District.objects.get(name="Кировский")
        other_microdistrict = Microdistrict.objects.get(district__name="Ленинский", name="Центр")
        self.client.force_login(self.user)
        response = self.client.post(reverse("data_quality"), {
            "listing_id": item.pk, "issue": "all", "address": item.address,
            "district_id": district.pk, "microdistrict_id": other_microdistrict.pk,
        })
        self.assertRedirects(response, reverse("data_quality") + "?issue=all")
        item.refresh_from_db()
        self.assertEqual(item.district_ref, district)
        self.assertIsNone(item.microdistrict_ref)

    def test_next_poll_time_is_shown_in_yekaterinburg_time(self):
        runs = next_poll_runs("avito", datetime(2026, 9, 8, 7, 0, tzinfo=datetime_timezone.utc))
        fast_run = runs[0][2]
        self.assertEqual((fast_run.hour, fast_run.minute), (12, 45))
        self.assertEqual(fast_run.tzinfo.key, "Asia/Yekaterinburg")

    def test_map_shows_only_visible_listings_with_coordinates(self):
        self.match.latitude, self.match.longitude = "54.738800", "55.972100"
        self.match.save(update_fields=["latitude", "longitude"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_map"))
        self.assertContains(response, 'data-lat="54.738800"')
        self.assertContains(response, "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=")
        self.assertContains(response, "Только выгодные")
        self.assertContains(response, "Подробнее")
        self.assertContains(response, "Открыть на сайте")
        self.assertContains(response, f'data-detail-url="{reverse("listing_detail", args=[self.match.pk])}"')
        self.assertNotContains(response, "В Дёмском")
