"""Общие расчёты сравнения рынка для web-интерфейса и Telegram."""

from dataclasses import dataclass
from statistics import median
from decimal import Decimal

from listings.models import Listing, ListingSnapshot, Microdistrict
from listings.services.microdistrict_polygons import canonical_microdistrict_name, normalized_microdistrict_label


MARKET_MIN_SIMILAR = 5
MARKET_MIN_ROOM_GROUP = 8
MARKET_MIN_DISTRICT_GROUP = 12
MIN_TARGET_AREA = Decimal("55")
MICRODISTRICT_MARKET_MIN_SAMPLE = 5


@dataclass(frozen=True)
class MarketComparison:
    """Результат выбора аналогов и сравнения цены объявления с рынком."""

    reference: str
    samples: int
    strict_samples: int
    fallback_samples: int
    confidence: str
    median_price: float
    delta_pct: int

    @property
    def discount_pct(self):
        """Возвращает отклонение в направлении, удобном для Telegram."""
        return -self.delta_pct


def _comparable_queryset():
    return (Listing.objects.filter(is_active=True, is_visible=True, price_per_sqm__isnull=False)
            .exclude(district__isnull=True).exclude(district="")
            .exclude(rooms__isnull=True).exclude(area__isnull=True))


def _comparison_for_rows(listing, rows):
    """Выбирает единую группу аналогов для веба и уведомлений."""
    if (listing.price_per_sqm is None or not listing.district
            or listing.rooms is None or listing.area is None):
        return None
    same_room = [(float(row["area"]), row["price_per_sqm"])
                 for row in rows if row["rooms"] == listing.rooms]
    similar = [(area, price) for area, price in same_room
               if abs(area - float(listing.area)) <= 10]
    strict_similar = [(area, price) for area, price in similar
                      if area >= float(MIN_TARGET_AREA)]
    target_is_large = float(listing.area) >= float(MIN_TARGET_AREA)
    if target_is_large and len(strict_similar) >= MARKET_MIN_SIMILAR:
        prices = [price for _, price in strict_similar]
        reference, confidence = "похожим квартирам от 55 м²", "high"
        strict_samples, fallback_samples = len(strict_similar), 0
    elif target_is_large and len(similar) >= MARKET_MIN_SIMILAR:
        prices = [price for _, price in similar]
        reference, confidence = "похожим квартирам (включая 45–54 м²)", "medium"
        strict_samples = len(strict_similar)
        fallback_samples = len(similar) - strict_samples
    elif len(similar) >= MARKET_MIN_SIMILAR:
        prices = [price for _, price in similar]
        reference, confidence = "похожим квартирам", "medium"
        strict_samples = fallback_samples = 0
    elif len(same_room) >= MARKET_MIN_ROOM_GROUP:
        prices = [price for _, price in same_room]
        location = "микрорайону" if listing.microdistrict else "району"
        reference, confidence = f"{location} и комнатности", "low"
        strict_samples = fallback_samples = 0
    else:
        local_prices = [row["price_per_sqm"] for row in rows]
        if len(local_prices) < MARKET_MIN_DISTRICT_GROUP:
            return None
        prices = local_prices
        location = "микрорайону" if listing.microdistrict else "району"
        reference, confidence = location, "low"
        strict_samples = fallback_samples = 0
    median_price = float(median(prices))
    delta_pct = round((float(listing.price_per_sqm) / median_price - 1) * 100)
    return MarketComparison(reference, len(prices), strict_samples, fallback_samples,
                            confidence, median_price, delta_pct)


def annotate_market_position(listings):
    """Annotate listings with the web-feed market comparison fields."""
    comparable = _comparable_queryset().values(
        "id", "district", "microdistrict", "rooms", "area", "price_per_sqm",
    )
    by_microdistrict, by_unclassified_district = {}, {}
    for row in comparable:
        entry = (row["id"], row["rooms"], float(row["area"]), row["price_per_sqm"])
        if row["microdistrict"]:
            by_microdistrict.setdefault((row["district"], row["microdistrict"]), []).append(entry)
        else:
            by_unclassified_district.setdefault(row["district"], []).append(entry)

    for listing in listings:
        listing.market_delta_pct = None
        listing.market_reference = ""
        listing.market_samples = 0
        listing.market_strict_samples = 0
        listing.market_fallback_samples = 0
        listing.market_confidence = "none"
        listing.market_state = ""
        listing.market_label = ""
        if (listing.price_per_sqm is None or not listing.district
                or listing.rooms is None or listing.area is None):
            continue
        if listing.microdistrict:
            local_prices = by_microdistrict.get((listing.district, listing.microdistrict), [])
        else:
            local_prices = by_unclassified_district.get(listing.district, [])
        rows = [{"microdistrict": listing.microdistrict, "rooms": rooms,
                 "area": area, "price_per_sqm": price}
                for pk, rooms, area, price in local_prices if pk != listing.pk]
        comparison = _comparison_for_rows(listing, rows)
        if comparison is None:
            continue
        listing.market_samples = comparison.samples
        listing.market_strict_samples = comparison.strict_samples
        listing.market_fallback_samples = comparison.fallback_samples
        listing.market_confidence = comparison.confidence
        listing.market_reference = comparison.reference
        listing.market_delta_pct = comparison.delta_pct
        if listing.market_delta_pct <= -4:
            listing.market_state = "below"
            listing.market_label = f"На {abs(listing.market_delta_pct)}% ниже рынка"
        elif listing.market_delta_pct >= 4:
            listing.market_state = "above"
            listing.market_label = f"На {listing.market_delta_pct}% выше рынка"
        else:
            listing.market_state = "neutral"
            listing.market_label = "В пределах рынка"
    return listings


def market_position_details(listing: Listing):
    """Возвращает совместимый с Telegram кортеж сравнения цены."""
    if (listing.price_per_sqm is None or not listing.district
            or listing.rooms is None or listing.area is None):
        return None
    rows = (_comparable_queryset().filter(district=listing.district).exclude(pk=listing.pk)
            .values("microdistrict", "rooms", "area", "price_per_sqm"))
    if listing.microdistrict:
        rows = [row for row in rows if row["microdistrict"] == listing.microdistrict]
    else:
        rows = [row for row in rows if not row["microdistrict"]]
    comparison = _comparison_for_rows(listing, rows)
    if comparison is None:
        return None
    return (comparison.discount_pct, comparison.reference, comparison.samples,
            round(comparison.median_price))


def microdistrict_market_stats(listings, period_since, filters):
    """Считает медианные показатели рынка по микрорайонам."""
    rows = (listings.exclude(district__isnull=True).exclude(district="")
            .exclude(microdistrict__isnull=True).exclude(microdistrict="")
            .exclude(price_per_sqm__isnull=True)
            .values("district", "microdistrict", "price_per_sqm", "price", "area", "first_seen_at", "is_visible"))
    directory_names = {
        canonical_microdistrict_name(item.name): item.name
        for item in Microdistrict.objects.all()
    }
    groups = {}
    display_names = {}
    district_names = {}
    for row in rows:
        key = canonical_microdistrict_name(row["microdistrict"])
        display_names[key] = directory_names.get(key, normalized_microdistrict_label(row["microdistrict"]))
        district_names.setdefault(key, set()).add(row["district"])
        groups.setdefault(key, []).append(row)
    all_sqm_prices = [row["price_per_sqm"] for entries in groups.values() for row in entries]
    overall_median = median(all_sqm_prices) if all_sqm_prices else None

    removed_counts = {}
    removals = (ListingSnapshot.objects.filter(observed_at__gte=period_since, listing__is_visible=True)
                .select_related("listing").only("data", "listing__source", "listing__district", "listing__microdistrict"))
    for snapshot in removals:
        if snapshot.data.get("is_active") is not False:
            continue
        if filters.get("source") and snapshot.listing.source != filters["source"]:
            continue
        if filters.get("district") and snapshot.listing.district != filters["district"]:
            continue
        key = canonical_microdistrict_name(snapshot.data.get("microdistrict") or snapshot.listing.microdistrict)
        if key in groups:
            removed_counts[key] = removed_counts.get(key, 0) + 1

    eligible_total = sum(len(entries) for entries in groups.values() if len(entries) >= MICRODISTRICT_MARKET_MIN_SAMPLE)
    stats = []
    for microdistrict, entries in groups.items():
        if len(entries) < MICRODISTRICT_MARKET_MIN_SAMPLE:
            continue
        sqm_prices = [entry["price_per_sqm"] for entry in entries]
        prices = [entry["price"] for entry in entries if entry["price"] is not None]
        areas = [float(entry["area"]) for entry in entries if entry["area"] is not None]
        median_sqm = int(median(sqm_prices))
        stats.append({
            "district": ", ".join(sorted(district_names.get(microdistrict, set()))),
            "microdistrict": display_names.get(microdistrict, microdistrict),
            "count": len(entries),
            "visible_count": sum(entry["is_visible"] for entry in entries),
            "hidden_count": sum(not entry["is_visible"] for entry in entries),
            "share_pct": round(len(entries) / eligible_total * 100, 1) if eligible_total else 0,
            "median_sqm": median_sqm, "median_price": int(median(prices)) if prices else None,
            "median_area": median(areas) if areas else None,
            "new_count": sum(entry["first_seen_at"] >= period_since for entry in entries),
            "removed_count": removed_counts.get(microdistrict, 0),
            "relative_pct": round((median_sqm / overall_median - 1) * 100) if overall_median else 0,
        })
    return sorted(stats, key=lambda row: (-row["median_sqm"], -row["count"], row["microdistrict"]))
