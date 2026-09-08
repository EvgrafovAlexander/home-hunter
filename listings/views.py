from datetime import timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from .models import Listing, Scan, SearchQuery


def _integer(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _decimal(value):
    try:
        return Decimal(value) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def filtered_listings(request, *, visible: bool | None = True):
    """Apply the same, deliberately small filter vocabulary to both screens."""
    listings = Listing.objects.all()
    if visible is not None:
        listings = listings.filter(is_visible=visible)
    names = ("source", "district", "rooms", "price_min", "price_max", "sqm_price_min",
             "sqm_price_max", "area_min", "area_max", "floor_min", "floor_max", "days", "active")
    values = {name: request.GET.get(name, "") for name in names}
    if values["active"] != "0":
        listings = listings.filter(is_active=True)
        values["active"] = "1"
    for field in ("source", "district"):
        if values[field]:
            listings = listings.filter(**{field: values[field]})
    number = _integer(values["rooms"])
    if number is not None:
        listings = listings.filter(rooms=number)
    for param, field in (("price_min", "price__gte"), ("price_max", "price__lte"),
                         ("sqm_price_min", "price_per_sqm__gte"), ("sqm_price_max", "price_per_sqm__lte"),
                         ("floor_min", "floor__gte"), ("floor_max", "floor__lte")):
        number = _integer(values[param])
        if number is not None:
            listings = listings.filter(**{field: number})
    for param, field in (("area_min", "area__gte"), ("area_max", "area__lte")):
        number = _decimal(values[param])
        if number is not None:
            listings = listings.filter(**{field: number})
    days = _integer(values["days"])
    if days in (1, 7, 30, 90):
        listings = listings.filter(first_seen_at__gte=timezone.now() - timedelta(days=days))
    else:
        values["days"] = ""
    return listings, values


def filter_options(*, visible: bool = True):
    listings = Listing.objects.filter(is_visible=visible)
    return {
        "sources": SearchQuery.Source.choices,
        "districts": list(listings.exclude(district__isnull=True).exclude(district="")
                          .order_by("district").values_list("district", flat=True).distinct()),
        "rooms": list(listings.exclude(rooms__isnull=True).order_by("rooms")
                      .values_list("rooms", flat=True).distinct()),
    }


@login_required
def listing_feed(request):
    listings, filters = filtered_listings(request)
    ordering = request.GET.get("sort", "new")
    sortings = {
        "new": "-first_seen_at", "price_up": "price", "price_down": "-price",
        "sqm_up": "price_per_sqm", "area_down": "-area",
    }
    if ordering not in sortings:
        ordering = "new"
    return render(request, "listings/feed.html", {
        "listings": listings.order_by(sortings[ordering], "-id")[:200],
        "result_count": listings.count(), "filters": filters, "filter_options": filter_options(),
        "sort": ordering,
    })


@login_required
def hidden_listing_feed(request):
    listings, filters = filtered_listings(request, visible=False)
    ordering = request.GET.get("sort", "new")
    sortings = {
        "new": "-first_seen_at", "price_up": "price", "price_down": "-price",
        "sqm_up": "price_per_sqm", "area_down": "-area",
    }
    if ordering not in sortings:
        ordering = "new"
    return render(request, "listings/feed.html", {
        "listings": listings.order_by(sortings[ordering], "-id")[:200],
        "result_count": listings.count(), "filters": filters, "filter_options": filter_options(visible=False),
        "sort": ordering, "hidden_feed": True,
    })


@login_required
def scan_statistics(request):
    sources = []
    for value, label in SearchQuery.Source.choices:
        sources.append({
            "label": label,
            "scans": Scan.objects.filter(source=value).select_related("search_query")
            .order_by("-started_at", "-id")[:3],
        })
    return render(request, "listings/scan_statistics.html", {"sources": sources})


@login_required
def dashboard(request):
    listings, filters = filtered_listings(request)
    prices = list(listings.exclude(price__isnull=True).values_list("price", flat=True))
    sqm_prices = list(listings.exclude(price_per_sqm__isnull=True).values_list("price_per_sqm", flat=True))
    since = timezone.now() - timedelta(days=13)
    daily_raw = (listings.filter(first_seen_at__gte=since).annotate(day=TruncDate("first_seen_at"))
                 .values("day").annotate(count=Count("id")).order_by("day"))
    by_day = {row["day"]: row["count"] for row in daily_raw}
    daily = []
    for offset in range(14):
        day = (since + timedelta(days=offset)).date()
        daily.append({"label": day.strftime("%d.%m"), "count": by_day.get(day, 0)})
    district_stats = list(listings.exclude(district__isnull=True).exclude(district="")
                          .exclude(price_per_sqm__isnull=True).values("district")
                          .annotate(count=Count("id"), average=Avg("price_per_sqm"))
                          .order_by("-count", "district")[:8])
    scatter = list(listings.exclude(area__isnull=True).exclude(price__isnull=True)
                   .order_by("-first_seen_at").values("area", "price")[:150])
    return render(request, "listings/dashboard.html", {
        "filters": filters, "filter_options": filter_options(), "total": listings.count(),
        "median_price": int(median(prices)) if prices else None,
        "median_sqm_price": int(median(sqm_prices)) if sqm_prices else None,
        "daily": daily, "daily_max": max((item["count"] for item in daily), default=1) or 1,
        "district_stats": district_stats, "scatter": scatter,
    })
