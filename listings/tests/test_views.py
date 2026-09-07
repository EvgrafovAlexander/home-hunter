from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from listings.models import Listing


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

    def test_dashboard_uses_filtered_population(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"), {"district": "Кировский"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["median_price"], 6_500_000)
