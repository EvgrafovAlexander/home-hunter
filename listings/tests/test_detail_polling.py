import pytest

from listings.models import Listing
from listings.services.detail_polling import select_detail_candidates


pytestmark = pytest.mark.django_db


def test_select_detail_candidates_keeps_source_and_excludes_completed():
    first = Listing.objects.create(
        source="avito", external_id="first", url="https://example.test/first",
        title="Первое", price=5_000_000, price_per_sqm=100_000,
        rooms=2, area="50.00", district="Кировский", is_active=True,
    )
    Listing.objects.create(
        source="cian", external_id="other", url="https://example.test/other",
        title="Другой источник", price=5_000_000, price_per_sqm=100_000,
        rooms=2, area="50.00", district="Кировский", is_active=True,
    )
    pending = Listing.objects.create(
        source="avito", external_id="pending", url="https://example.test/pending",
        title="Ожидает проверки", price=5_000_000, price_per_sqm=100_000,
        rooms=2, area="50.00", district="Кировский", is_active=True,
    )

    candidates = select_detail_candidates("avito", "avito_detail_state", [first.pk], 5)

    assert [item.pk for item in candidates] == [pending.pk]
