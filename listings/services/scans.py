import logging
from asgiref.sync import async_to_sync
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from collectors.base import BaseCollector, CollectionError
from listings.models import CianFullScanCheckpoint, Listing, ListingSearchQuery, ListingSnapshot, Scan, SearchQuery
from .persistence import process_listing, snapshot_data_from_listing

logger = logging.getLogger(__name__)


def update_scan_progress(scan_id: int, pages_scanned: int, items_seen: int, unique_items_seen: int) -> None:
    """Persist progress while a long CIAN batch is still running."""
    Scan.objects.filter(pk=scan_id, status=Scan.Status.RUNNING).update(
        pages_scanned=pages_scanned,
        items_seen=items_seen,
        unique_items_seen=unique_items_seen,
    )


@transaction.atomic
def finalize_full_scan(search: SearchQuery, seen_ids: set[int]) -> None:
    affected = list(search.listing_relations.values_list("listing_id", flat=True))
    search.listing_relations.exclude(listing_id__in=seen_ids).update(is_active=False)
    search.listing_relations.filter(listing_id__in=seen_ids).update(is_active=True)
    active = ListingSearchQuery.objects.filter(listing_id__in=affected, is_active=True).values("listing_id")
    deactivated = list(Listing.objects.filter(pk__in=affected, is_active=True).exclude(pk__in=active))
    observed_at = timezone.now()
    ListingSnapshot.objects.bulk_create([
        ListingSnapshot(listing=listing, observed_at=observed_at,
                        data=snapshot_data_from_listing(listing, is_active=False))
        for listing in deactivated
    ])
    Listing.objects.filter(pk__in=affected).update(is_active=False)
    Listing.objects.filter(pk__in=active).update(is_active=True)


def run_scan(search: SearchQuery, collector: BaseCollector, *, mode: str) -> Scan:
    scan = Scan.objects.create(search_query=search, source=search.source, mode=mode)
    logger.info("scan start id=%s search=%s mode=%s", scan.pk, search.name, mode)
    try:
        checkpoint = None
        start_page = 1
        if search.source == SearchQuery.Source.CIAN and mode == Scan.Mode.FULL:
            checkpoint, _ = CianFullScanCheckpoint.objects.get_or_create(
                search_query=search, defaults={"search_url": search.url},
            )
            if checkpoint.search_url != search.url:
                checkpoint.search_url, checkpoint.next_page = search.url, 1
                checkpoint.expected_total, checkpoint.external_ids = None, []
                checkpoint.save()
            start_page = checkpoint.next_page
        collection_error = None
        try:
            kwargs = {"start_page": start_page} if checkpoint else {}
            if checkpoint:
                async def progress_callback(pages_scanned, items_seen, unique_items_seen):
                    await sync_to_async(update_scan_progress)(
                        scan.pk, pages_scanned, items_seen, unique_items_seen,
                    )
                kwargs["progress_callback"] = progress_callback
            result = async_to_sync(collector.collect)(search, mode=mode, **kwargs)
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
            if checkpoint and checkpoint.expected_total not in (None, result.expected_total):
                checkpoint.next_page, checkpoint.expected_total, checkpoint.external_ids = 1, None, []
                checkpoint.save()
            raise collection_error
        if checkpoint and not collection_error:
            checkpoint.refresh_from_db()
            if checkpoint.expected_total not in (None, result.expected_total):
                # The search changed while batches were being collected: start a
                # fresh pass later, never deactivate from a mixed result set.
                checkpoint.next_page, checkpoint.expected_total, checkpoint.external_ids = 1, None, []
                checkpoint.save()
                raise RuntimeError("CIAN total changed; checkpoint reset")
            checkpoint.expected_total = result.expected_total
            checkpoint.external_ids = sorted(set(checkpoint.external_ids).union(
                external_id for _, external_id in seen_keys
            ))
            if result.complete:
                if len(checkpoint.external_ids) != checkpoint.expected_total:
                    checkpoint.next_page = 1
                    checkpoint.expected_total, checkpoint.external_ids = None, []
                    checkpoint.save()
                    raise RuntimeError("CIAN full checkpoint count does not match total")
                completed_keys = set(checkpoint.external_ids)
                seen_ids = set(ListingSearchQuery.objects.filter(
                    search_query=search, listing__source=SearchQuery.Source.CIAN,
                    listing__external_id__in=completed_keys,
                ).values_list("listing_id", flat=True))
                checkpoint.delete()
            else:
                checkpoint.next_page = result.next_page
                checkpoint.save()
        if mode == Scan.Mode.FULL and not result.complete and not checkpoint:
            raise RuntimeError(f"Partial full scan: {result.stop_reason}")
        with transaction.atomic():
            # CIAN pages are a live, shifting result set.  A multi-run pass is
            # useful for collecting data, but is not proof that an unseen
            # listing disappeared, so it must never deactivate CIAN records.
            if (mode == Scan.Mode.FULL and result.complete
                    and search.source != SearchQuery.Source.CIAN):
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
    try:
        from listings.services.scan_health import check_source_health
        check_source_health(scan.source)
    except Exception:
        logger.exception("health notification check failed for source=%s", scan.source)
    return scan
