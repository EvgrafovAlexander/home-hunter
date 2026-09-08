from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from listings.models import Listing, PriceHistory, Scan, SearchQuery


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

    def test_feed_excludes_hidden_listing_and_separate_feed_shows_it(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("listing_feed"))
        self.assertNotContains(response, "В Дёмском")
        response = self.client.get(reverse("hidden_listing_feed"))
        self.assertContains(response, "В Дёмском")

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
