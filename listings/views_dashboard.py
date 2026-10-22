"""HTTP-обработчики dashboard и связанные с ним агрегаты."""

from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max
from django.shortcuts import render
from django.utils import timezone

from .models import Listing
from .services.dashboard import (
    build_daily_series,
    build_district_stats,
    build_distribution,
    build_price_changes,
)
from .services.listing_queries import filtered_listings, filter_options
from .services.market import MICRODISTRICT_MARKET_MIN_SAMPLE, microdistrict_market_stats


def _integer(value):
    """Преобразовать значение формы в целое число."""
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@login_required
def dashboard(request):
    """Показать агрегаты объявлений и рыночную статистику."""
    listings, filters = filtered_listings(request)
    # KPI используют видимую выборку, а медианы рынка — полный baseline.
    market_listings, _ = filtered_listings(request, visible=None)
    period_days = _integer(request.GET.get("stats_days", 30))
    if period_days not in (7, 30, 90):
        period_days = 30
    period_since = timezone.now() - timedelta(days=period_days - 1)
    prices = list(listings.exclude(price__isnull=True).values_list("price", flat=True))
    sqm_prices = list(listings.exclude(price_per_sqm__isnull=True).values_list("price_per_sqm", flat=True))
    daily = build_daily_series(listings, period_since, period_days)
    district_stats = build_district_stats(listings, period_since)
    changes, price_drops, price_increases, price_history_count = build_price_changes(listings, period_since)
    microdistrict_stats = microdistrict_market_stats(market_listings, period_since, filters)

    area_market_stats = []
    for label, lower, upper in (("45–54 м²", Decimal("45"), Decimal("55")),
                                ("55–64 м²", Decimal("55"), Decimal("65")),
                                ("65+ м²", Decimal("65"), None)):
        segment = market_listings.filter(area__gte=lower)
        if upper is not None:
            segment = segment.filter(area__lt=upper)
        segment_prices = list(segment.exclude(price_per_sqm__isnull=True).values_list("price_per_sqm", flat=True))
        area_market_stats.append({
            "label": label, "count": segment.count(),
            "median_sqm": int(median(segment_prices)) if segment_prices else None,
        })

    price_distribution = build_distribution(
        prices, buckets=7,
        formatter=lambda low, high: f"{round(low / 1_000_000, 1):g}–{round(high / 1_000_000, 1):g} млн",
    )
    area_distribution = build_distribution(
        [float(value) for value in listings.exclude(area__isnull=True).values_list("area", flat=True)],
        buckets=6,
        formatter=lambda low, high: f"{round(low):g}–{round(high):g}",
    )
    rooms = dict(listings.filter(rooms__in=(2, 3)).values("rooms").annotate(count=Count("id")).values_list("rooms", "count"))
    rooms_two, rooms_three = rooms.get(2, 0), rooms.get(3, 0)
    total_rooms = rooms_two + rooms_three
    return render(request, "listings/dashboard.html", {
        "filters": filters, "filter_options": filter_options(), "total": listings.count(),
        "median_price": int(median(prices)) if prices else None,
        "median_sqm_price": int(median(sqm_prices)) if sqm_prices else None,
        "period_days": period_days, "daily": daily,
        "daily_max": max((item["count"] for item in daily), default=1) or 1,
        "district_stats": district_stats,
        "microdistrict_stats": microdistrict_stats,
        "area_market_stats": area_market_stats,
        "microdistrict_min_sample": MICRODISTRICT_MARKET_MIN_SAMPLE,
        "price_drop_count": len(price_drops), "price_unchanged_count": max(0, price_history_count - len(changes)),
        "price_increase_count": sum(change["difference"] > 0 for change in changes),
        "price_drops": price_drops, "price_increases": price_increases,
        "price_distribution": price_distribution, "area_distribution": area_distribution,
        "distribution_max": max([1, *(row["count"] for row in price_distribution + area_distribution)]),
        "rooms_two": rooms_two, "rooms_three": rooms_three, "rooms_total": total_rooms,
        "rooms_two_pct": round(rooms_two / total_rooms * 100) if total_rooms else 0,
        "last_updated": listings.aggregate(value=Max("last_seen_at"))["value"],
    })
