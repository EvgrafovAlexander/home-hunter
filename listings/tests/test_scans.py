from dataclasses import replace
import pytest
from django.utils import timezone
from collectors.base import BaseCollector, CollectionError, CollectionResult
from listings.models import ListingSearchQuery, ListingSnapshot, Scan, SearchQuery
from listings.services.persistence import process_listing
from listings.services.scans import run_scan
from listings.services.change_history import change_events
from .test_persistence import search, item

pytestmark = pytest.mark.django_db(transaction=True)


class FakeCollector(BaseCollector):
    def __init__(self, result, fail=False):
        self.result, self.fail = result, fail

    async def collect(self, search, *, mode, **kwargs):
        if self.fail:
            raise CollectionError("blocked", self.result)
        return self.result


def test_full_deactivates_only_missing(search, item):
    missing = process_listing(search, item, timezone.now()).listing
    found = replace(item, external_id="2")
    scan = run_scan(search, FakeCollector(CollectionResult([found], 2, 1, True)), mode="full")
    missing.refresh_from_db()
    assert scan.status == "success" and scan.new_items == 1
    assert not missing.is_active
    assert not ListingSearchQuery.objects.get(listing=missing).is_active
    assert ListingSnapshot.objects.filter(listing=missing, data__is_active=False).exists()


def test_cian_full_never_deactivates_missing_listing(item):
    search = SearchQuery.objects.create(name="CIAN", source="cian", url="https://ufa.cian.ru/cat.php?region=1")
    missing = process_listing(search, replace(item, source="cian"), timezone.now()).listing
    scan = run_scan(search, FakeCollector(CollectionResult(complete=True, expected_total=0)), mode="full")
    missing.refresh_from_db()
    assert scan.status == "success"
    assert missing.is_active and ListingSearchQuery.objects.get(listing=missing).is_active


def test_cian_full_deactivates_listing_after_two_completed_passes(item):
    search = SearchQuery.objects.create(name="CIAN", source="cian", url="https://ufa.cian.ru/cat.php?region=1")
    missing = process_listing(search, replace(item, source="cian"), timezone.now()).listing
    complete_empty = CollectionResult(complete=True, expected_total=0)

    run_scan(search, FakeCollector(complete_empty), mode="full")
    relation = ListingSearchQuery.objects.get(listing=missing, search_query=search)
    assert relation.is_active and relation.missed_full_scans == 1

    run_scan(search, FakeCollector(complete_empty), mode="full")
    missing.refresh_from_db()
    relation.refresh_from_db()
    assert not relation.is_active and not missing.is_active
    snapshot = ListingSnapshot.objects.get(listing=missing, data__is_active=False)
    assert snapshot.data["deactivation_reason"] == "Не найдено в двух завершённых full-проходах ЦИАН"
    assert any(change["label"] == snapshot.data["deactivation_reason"]
               for event in change_events([*missing.snapshots.all()]) for change in event["changes"])


@pytest.mark.parametrize("mode,complete,fail", [("fast", True, False), ("full", False, False), ("full", True, True)])
def test_partial_failed_fast_do_not_deactivate(search, item, mode, complete, fail):
    listing = process_listing(search, item, timezone.now()).listing
    scan = run_scan(search, FakeCollector(CollectionResult([], 1, 0, complete), fail), mode=mode)
    listing.refresh_from_db()
    assert listing.is_active and ListingSearchQuery.objects.get().is_active
    assert scan.status == ("success" if mode == "fast" else "failed")
    assert scan.finished_at is not None


def test_other_search_keeps_listing_active(search, item):
    listing = process_listing(search, item, timezone.now()).listing
    other = SearchQuery.objects.create(name="Other", source="avito", url=search.url)
    process_listing(other, item, timezone.now())
    run_scan(search, FakeCollector(CollectionResult(complete=True)), mode="full")
    listing.refresh_from_db()
    assert listing.is_active
    run_scan(other, FakeCollector(CollectionResult(complete=True)), mode="full")
    listing.refresh_from_db()
    assert not listing.is_active
    process_listing(search, item, timezone.now())
    listing.refresh_from_db()
    assert listing.is_active


def test_failed_scan_keeps_observations_and_deduplicates(search, item):
    scan = run_scan(search, FakeCollector(CollectionResult([item, item], 1, 2), True), mode="full")
    assert scan.status == "failed"
    assert scan.items_seen == 2 and scan.unique_items_seen == scan.new_items == 1
    assert "blocked" in scan.error
