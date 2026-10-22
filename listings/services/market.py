"""Shared market-comparison calculations used by web and Telegram delivery."""

from statistics import median
from decimal import Decimal

from listings.models import Listing


MARKET_MIN_SIMILAR = 5
MARKET_MIN_ROOM_GROUP = 8
MARKET_MIN_DISTRICT_GROUP = 12
MIN_TARGET_AREA = Decimal("55")


def _comparable_queryset():
    return (Listing.objects.filter(is_active=True, is_visible=True, price_per_sqm__isnull=False)
            .exclude(district__isnull=True).exclude(district="")
            .exclude(rooms__isnull=True).exclude(area__isnull=True))


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
            location_label = "микрорайону"
        else:
            local_prices = by_unclassified_district.get(listing.district, [])
            location_label = "району"
        same_room = [(area, price) for pk, rooms, area, price in local_prices
                     if pk != listing.pk and rooms == listing.rooms]
        all_local_prices = [price for pk, _, _, price in local_prices if pk != listing.pk]
        similar = [(area, price) for area, price in same_room if abs(area - float(listing.area)) <= 10]
        strict_similar = [(area, price) for area, price in similar if area >= float(MIN_TARGET_AREA)]
        if float(listing.area) >= float(MIN_TARGET_AREA) and len(strict_similar) >= MARKET_MIN_SIMILAR:
            prices, reference = [price for _, price in strict_similar], "похожим квартирам от 55 м²"
            listing.market_strict_samples = len(strict_similar)
            listing.market_confidence = "high"
        elif float(listing.area) >= float(MIN_TARGET_AREA) and len(similar) >= MARKET_MIN_SIMILAR:
            prices, reference = [price for _, price in similar], "похожим квартирам (включая 45–54 м²)"
            listing.market_strict_samples = len(strict_similar)
            listing.market_fallback_samples = len(similar) - len(strict_similar)
            listing.market_confidence = "medium"
        elif len(similar) >= MARKET_MIN_SIMILAR:
            prices, reference = [price for _, price in similar], "похожим квартирам"
            listing.market_confidence = "medium"
        elif len(same_room) >= MARKET_MIN_ROOM_GROUP:
            prices, reference = [price for _, price in same_room], f"{location_label} и комнатности"
            listing.market_confidence = "low"
        elif len(all_local_prices) >= MARKET_MIN_DISTRICT_GROUP:
            prices, reference = all_local_prices, location_label
            listing.market_confidence = "low"
        else:
            continue
        listing.market_samples = len(prices)
        if not listing.market_strict_samples and float(listing.area) >= float(MIN_TARGET_AREA):
            listing.market_strict_samples = len([price for area, price in similar if area >= float(MIN_TARGET_AREA)])
        if not listing.market_fallback_samples and float(listing.area) >= float(MIN_TARGET_AREA):
            listing.market_fallback_samples = max(0, listing.market_samples - listing.market_strict_samples)
        listing.market_reference = reference
        listing.market_delta_pct = round((float(listing.price_per_sqm) / float(median(prices)) - 1) * 100)
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
    """Return the Telegram/deal comparison tuple for one listing."""
    if (listing.price_per_sqm is None or not listing.district
            or listing.rooms is None or listing.area is None):
        return None
    rows = (_comparable_queryset().filter(district=listing.district).exclude(pk=listing.pk)
            .values("microdistrict", "rooms", "area", "price_per_sqm"))
    if listing.microdistrict:
        rows = [row for row in rows if row["microdistrict"] == listing.microdistrict]
        location = "микрорайону"
    else:
        rows = [row for row in rows if not row["microdistrict"]]
        location = "району"
    same_room = [row for row in rows if row["rooms"] == listing.rooms]
    similar = [row["price_per_sqm"] for row in same_room if abs(float(row["area"]) - float(listing.area)) <= 10]
    if len(similar) >= MARKET_MIN_SIMILAR:
        prices, reference = similar, "похожим квартирам"
    elif len(same_room) >= MARKET_MIN_ROOM_GROUP:
        prices, reference = [row["price_per_sqm"] for row in same_room], f"{location} и комнатности"
    elif len(rows) >= MARKET_MIN_DISTRICT_GROUP:
        prices, reference = [row["price_per_sqm"] for row in rows], location
    else:
        return None
    median_price = round(float(median(prices)))
    discount = round((1 - float(listing.price_per_sqm) / median_price) * 100)
    return discount, reference, len(prices), median_price
