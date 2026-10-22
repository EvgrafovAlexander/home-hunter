"""Сервис подготовки агрегатов для операционного dashboard Home Hunter."""

from datetime import timedelta
from statistics import median

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from listings.models import PriceHistory


def build_daily_series(listings, period_since, period_days):
    """Заполняет ежедневный ряд нулями за дни без новых объявлений."""
    daily_raw = (listings.filter(first_seen_at__gte=period_since).annotate(day=TruncDate("first_seen_at"))
                 .values("day").annotate(count=Count("id")).order_by("day"))
    by_day = {row["day"]: row["count"] for row in daily_raw}
    return [
        {"label": (period_since + timedelta(days=offset)).strftime("%d.%m"),
         "count": by_day.get((period_since + timedelta(days=offset)).date(), 0)}
        for offset in range(period_days)
    ]


def build_district_stats(listings, period_since):
    """Считает медианы и новые объявления по районам без микрорайона."""
    rows = list(listings.filter(Q(microdistrict__isnull=True) | Q(microdistrict=""))
                .exclude(district__isnull=True).exclude(district="")
                .values("district", "price_per_sqm", "price", "area", "first_seen_at"))
    groups = {}
    for row in rows:
        groups.setdefault(row["district"], []).append(row)
    stats = []
    for district, entries in groups.items():
        prices = [row["price"] for row in entries if row["price"] is not None]
        sqm_prices = [row["price_per_sqm"] for row in entries if row["price_per_sqm"] is not None]
        areas = [float(row["area"]) for row in entries if row["area"] is not None]
        stats.append({
            "district": district,
            "count": len(entries),
            "median_price": int(median(prices)) if prices else None,
            "median_sqm": int(median(sqm_prices)) if sqm_prices else None,
            "median_area": median(areas) if areas else None,
            "new_count": sum(row["first_seen_at"] >= period_since for row in entries if row["first_seen_at"] is not None),
        })
    return sorted(stats, key=lambda row: (-row["count"], row["district"]))[:7]


def build_price_changes(listings, period_since):
    """Собирает первые/последние цены и десятку крупнейших изменений."""
    history = (PriceHistory.objects.filter(listing__in=listings, observed_at__gte=period_since,
                                            price__isnull=False)
               .select_related("listing").order_by("listing_id", "observed_at", "id"))
    changes_by_listing = {}
    for observation in history:
        change = changes_by_listing.setdefault(
            observation.listing_id,
            {"listing": observation.listing, "first": observation.price, "last": observation.price},
        )
        change["last"] = observation.price
    changes = [{**change, "difference": change["last"] - change["first"]}
               for change in changes_by_listing.values() if change["last"] != change["first"]]
    drops = sorted(
        ({**change, "percent": round(change["difference"] / change["first"] * 100) if change["first"] else 0}
         for change in changes if change["difference"] < 0),
        key=lambda change: change["difference"],
    )[:10]
    increases = sorted(
        ({**change, "percent": round(change["difference"] / change["first"] * 100) if change["first"] else 0}
         for change in changes if change["difference"] > 0),
        key=lambda change: change["difference"], reverse=True,
    )[:10]
    return changes, drops, increases, len(changes_by_listing)


def build_distribution(values, *, buckets, formatter):
    """Строит равномерные ценовые или площадные интервалы."""
    if not values:
        return []
    low, high = min(values), max(values)
    step = max(1, (high - low) / buckets)
    rows = [{"label": formatter(low + step * index, low + step * (index + 1)), "count": 0}
            for index in range(buckets)]
    for value in values:
        rows[min(buckets - 1, int((value - low) / step))]["count"] += 1
    return rows
