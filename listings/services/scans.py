import logging
from asgiref.sync import async_to_sync
from django.db import transaction
from django.utils import timezone

from collectors.base import BaseCollector, CollectionError
from listings.models import Listing, ListingSearchQuery, Scan, SearchQuery
from .persistence import process_listing

logger = logging.getLogger(__name__)


@transaction.atomic
def finalize_full_scan(search: SearchQuery, seen_ids: set[int]) -> None:
    affected = list(search.listing_relations.values_list("listing_id", flat=True))
    search.listing_relations.exclude(listing_id__in=seen_ids).update(is_active=False)
    search.listing_relations.filter(listing_id__in=seen_ids).update(is_active=True)
    active = ListingSearchQuery.objects.filter(listing_id__in=affected, is_active=True).values("listing_id")
    Listing.objects.filter(pk__in=affected).update(is_active=False)
    Listing.objects.filter(pk__in=active).update(is_active=True)


def run_scan(search: SearchQuery, collector: BaseCollector, *, mode: str) -> Scan:
    scan = Scan.objects.create(search_query=search, source=search.source, mode=mode)
    logger.info("scan start id=%s search=%s mode=%s", scan.pk, search.name, mode)
    try:
        collection_error = None
        try:
            result = async_to_sync(collector.collect)(search, mode=mode)
        except CollectionError as exc:
            result = exc.result
            collection_error = exc
        scan.pages_scanned = result.pages_scanned
        scan.items_seen = result.items_seen
        seen_keys = set()
        seen_ids = set()
        for item in result.listings:
            key = (item.source, item.external_id)
            if key in seen_keys:
                continue
            saved = process_listing(search, item, timezone.now())
            seen_keys.add(key)
            seen_ids.add(saved.listing.pk)
            scan.unique_items_seen += 1
            scan.new_items += int(saved.created)
            scan.updated_items += int(saved.updated)
            scan.price_changes += int(saved.price_changed)
        if collection_error:
            raise collection_error
        if mode == Scan.Mode.FULL and not result.complete:
            raise RuntimeError(f"Partial full scan: {result.stop_reason}")
        with transaction.atomic():
            if mode == Scan.Mode.FULL:
                finalize_full_scan(search, seen_ids)
            scan.status = Scan.Status.SUCCESS
            scan.finished_at = timezone.now()
            scan.save()
    except Exception as exc:
        scan.status = Scan.Status.FAILED
        scan.error = f"{type(exc).__name__}: {str(exc)[:800]}"
        scan.finished_at = timezone.now()
        scan.save()
        logger.error("scan failed id=%s: %s", scan.pk, scan.error)
    logger.info("scan finished id=%s status=%s new=%s updated=%s price_changes=%s",
                scan.pk, scan.status, scan.new_items, scan.updated_items, scan.price_changes)
    return scan
