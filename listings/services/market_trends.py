"""Сохраняет дневные наблюдения и рассчитывает динамику выбранного сегмента.

Не восстанавливает неизвестное прошлое и не интерпретирует снятие как продажу.
HTTP и отрисовка графиков остаются на границе представления.
"""

import hashlib
import json
from datetime import datetime, timedelta
from statistics import median

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from listings.models import Listing, ListingSearchQuery, MarketDailySnapshot, Scan, SearchQuery

from .market_exclusions import excluded_listing_ids

VERSION = 1
SEGMENT_FIELDS = ("district", "microdistrict", "rooms", "area")


@transaction.atomic
def capture_market_snapshot():
    """Атомарно сохраняет текущие наблюдения; повтор за тот же день не меняет историю."""
    now = timezone.now()
    day = timezone.localdate(now)
    result = []
    for source in SearchQuery.Source.values:
        existing = MarketDailySnapshot.objects.filter(day=day, source=source).first()
        if existing:
            result.append(existing)
            continue
        searches = list(SearchQuery.objects.filter(source=source, enabled=True).order_by("pk")
                        .values("id", "url", "mobile_api_url"))
        search_ids = [search["id"] for search in searches]
        # В истории хранится отпечаток охвата, а не URL с возможными credentials.
        scope_hash = hashlib.sha256(json.dumps(searches, sort_keys=True).encode()).hexdigest()
        relations = ListingSearchQuery.objects.filter(search_query_id__in=search_ids)
        active_relations = relations.filter(listing_id=OuterRef("pk"), is_active=True)
        rows = list(Listing.objects.filter(source=source, pk__in=relations.values("listing_id"), first_seen_at__lte=now)
                    .annotate(in_search=Exists(active_relations)).order_by("pk")
                    .values("id", "price", "area", "rooms", "district", "microdistrict",
                            "is_active", "publication_status", "in_search", "first_seen_at"))
        for row in rows:
            row["active"] = (row["is_active"] and row["publication_status"] == "published"
                             and row["in_search"])
            for key in ("is_active", "publication_status", "in_search"):
                row.pop(key)
            row["area"] = float(row["area"]) if row["area"] is not None else None
            row["first_seen_at"] = row["first_seen_at"].isoformat()
        scans = Scan.objects.filter(search_query_id__in=search_ids, started_at__gte=now - timedelta(days=1),
                                    started_at__lte=now)
        success_count = scans.filter(status=Scan.Status.SUCCESS, finished_at__lte=now).values(
            "search_query_id").distinct().count()
        quality = {"search_count": len(search_ids), "successful_searches": success_count,
                   "failed_scans": scans.filter(status=Scan.Status.FAILED).count(),
                   "running_scans": scans.filter(status=Scan.Status.RUNNING).count(),
                   "coverage": "unverified"}
        snapshot, _ = MarketDailySnapshot.objects.get_or_create(day=day, source=source, defaults={
            "captured_at": now, "scope_hash": scope_hash, "quality": quality,
            "methodology_version": VERSION, "listings": rows,
        })
        result.append(snapshot)
    return result


def _matches(row, filters):
    if row["id"] in filters.get("_excluded_ids", ()):
        return False
    for key in ("district", "microdistrict", "rooms"):
        if filters.get(key) not in (None, "") and str(row.get(key)) != str(filters[key]):
            return False
    area = row.get("area")
    for key, minimum in (("area_min", True), ("area_max", False)):
        value = filters.get(key)
        if value is not None and (area is None or (area < value if minimum else area > value)):
            return False
    return True


def _quantile(values, fraction):
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower))


def _weekly_point(day, snapshots, filters):
    snapshot = snapshots.get(day)
    point = {"day": day.isoformat(), "median": None, "q25": None, "q75": None,
             "active": None, "samples": None, "new": None, "removed": None,
             "reduced_pct": None, "matched_change": None, "matched_count": None,
             "reason": "Нет дневного среза", "quality": None, "scope_changed": False}
    if snapshot is None:
        return point
    rows = [row for row in snapshot.listings if _matches(row, filters)]
    active = [row for row in rows if row["active"]]
    prices = sorted(row["price"] / row["area"] for row in active
                    if row.get("price") and row["price"] > 0 and row.get("area") and row["area"] > 0)
    point.update(active=len(active), samples=len(prices), quality=snapshot.quality,
                 captured_at=snapshot.captured_at.isoformat(),
                 scope=f"{snapshot.scope_hash}:{snapshot.methodology_version}")
    if prices:
        point.update(median=round(median(prices)), q25=_quantile(prices, .25), q75=_quantile(prices, .75))
    interval = [snapshots.get(day - timedelta(days=offset)) for offset in range(8)]
    if any(item is not None and (item.scope_hash, item.methodology_version) !=
           (snapshot.scope_hash, snapshot.methodology_version) for item in interval):
        point.update(reason="Изменился охват поиска или методика", scope_changed=True)
        return point
    if any(item is None for item in interval):
        point["reason"] = "Для сравнения нужны 8 последовательных дневных срезов"
        return point
    if any(not item.quality.get("search_count") or
           item.quality.get("successful_searches", 0) < item.quality["search_count"] for item in interval):
        point["reason"] = "Есть дни без успешного опроса каждого поиска"
        return point
    previous = interval[-1]
    before = {row["id"]: row for row in previous.listings if _matches(row, filters)}
    after = {row["id"]: row for row in rows}
    matched = [(row, after[pk]) for pk, row in before.items() if pk in after and row["active"]
               and after[pk]["active"] and row.get("price", 0) and row["price"] > 0
               and after[pk].get("price", 0) and after[pk]["price"] > 0
               and all(row.get(key) == after[pk].get(key) for key in SEGMENT_FIELDS)]
    changes = [(new["price"] / old["price"] - 1) * 100 for old, new in matched]
    point.update(
        new=sum(previous.captured_at < datetime.fromisoformat(row["first_seen_at"]) <= snapshot.captured_at
                for row in rows),
        removed=sum(row["active"] and pk in after and not after[pk]["active"] for pk, row in before.items()),
        matched_count=len(changes), reason="Наблюдаемые изменения; полнота охвата не подтверждена",
    )
    if changes:
        point.update(reduced_pct=round(100 * sum(change < 0 for change in changes) / len(changes), 1),
                     matched_change=round(median(changes), 2))
    return point


def market_trends_context(filters, *, today=None):
    """Возвращает недельные точки и исторические варианты фильтров без личных скрытий."""
    filters = dict(filters)
    excluded = set(excluded_listing_ids())
    filters["_excluded_ids"] = set() if filters.get("include_excluded") else excluded
    today = today or timezone.localdate()
    weeks = filters.get("weeks", 12)
    start = today - timedelta(weeks=weeks)
    snapshots = list(MarketDailySnapshot.objects.filter(source=filters["source"], day__gte=start - timedelta(days=7),
                                                         day__lte=today).order_by("day"))
    by_day = {snapshot.day: snapshot for snapshot in snapshots}
    options = {key: set() for key in ("district", "microdistrict", "rooms")}
    for snapshot in snapshots:
        for row in snapshot.listings:
            if row["id"] in filters["_excluded_ids"]:
                continue
            for key in options:
                if row.get(key) not in (None, ""):
                    options[key].add(row[key])
    # Законченные недели представлены воскресеньем; текущая — последним наблюдением.
    end = today - timedelta(days=(today.weekday() + 1) % 7)
    days = [end - timedelta(weeks=offset) for offset in reversed(range(weeks))]
    latest = snapshots[-1].day if snapshots else None
    if latest and latest > end:
        days.append(latest)
    points = [_weekly_point(day, by_day, filters) for day in days]
    return {"points": points, "options": {key: sorted(values) for key, values in options.items()},
            "latest": snapshots[-1] if snapshots else None, "filters": filters,
            "sources": SearchQuery.Source.choices, "has_data": bool(snapshots),
            "excluded_count": len({row["id"] for snapshot in snapshots for row in snapshot.listings
                                   if row["id"] in excluded}), "include_excluded": bool(filters.get("include_excluded"))}
