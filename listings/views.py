from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Listing, ManualDomclickJob, PriceHistory, Scan, SearchQuery, SourcePollingControl
from .services.polling import default_polling_enabled


SCAN_STALE_AFTER = timedelta(hours=6)
SCHEDULE_TIME_ZONE = ZoneInfo("Europe/Moscow")
DISPLAY_TIME_ZONE = ZoneInfo("Asia/Yekaterinburg")
MARKET_MIN_SIMILAR = 5
MARKET_MIN_ROOM_GROUP = 8
MARKET_MIN_DISTRICT_GROUP = 12


def _next_at(now, hour: int, minute: int):
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _next_hourly_at(now, minute: int):
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(hours=1)


def _next_every_three_hours_at(now, minute: int):
    """Return the next ``00/3:<minute>`` systemd calendar slot."""
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    hours_to_add = (-candidate.hour) % 3
    candidate += timedelta(hours=hours_to_add)
    return candidate if candidate > now else candidate + timedelta(hours=3)


def _next_avito_full(now):
    for offset in range(4):
        day = now.date() + timedelta(days=offset)
        if (day.day - 1) % 3 == 0:
            candidate = timezone.make_aware(datetime.combine(day, time(3, 30)), now.tzinfo)
            if candidate > now:
                return candidate


def next_poll_runs(source: str, now):
    """Mirror systemd timers (MSK) and present their dates in Yekaterinburg time."""
    now = now.astimezone(SCHEDULE_TIME_ZONE)
    if source == SearchQuery.Source.AVITO:
        runs = [(Scan.Mode.FAST, "Быстрый", _next_every_three_hours_at(now, 15), ""),
                (Scan.Mode.FULL, "Полный", _next_avito_full(now), "")]
    elif source == SearchQuery.Source.CIAN:
        runs = [
            (Scan.Mode.FAST, "Быстрый", _next_hourly_at(now, 0), "в течение 5 мин."),
            (Scan.Mode.FULL, "Полный", _next_at(now, 0, 10), "в течение 20 мин."),
        ]
    elif source == SearchQuery.Source.DOMCLICK:
        runs = [(Scan.Mode.FAST, "Быстрый", _next_hourly_at(now, 15), "")]
    else:
        return []
    return [(mode, label, scheduled_at.astimezone(DISPLAY_TIME_ZONE), delay) for mode, label, scheduled_at, delay in runs]


def polling_mode_locked(source: str, mode: str) -> bool:
    return source == SearchQuery.Source.AVITO and mode == Scan.Mode.FULL


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


def add_market_position(listings):
    """Compare within a microdistrict first; avoid mixing distinct local markets."""
    comparable = (Listing.objects.filter(is_active=True, price_per_sqm__isnull=False)
                  .exclude(district__isnull=True).exclude(district="")
                  .exclude(rooms__isnull=True).exclude(area__isnull=True)
                  .values("id", "district", "microdistrict", "rooms", "area", "price_per_sqm"))
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
        similar = [price for area, price in same_room if abs(area - float(listing.area)) <= 10]
        if len(similar) >= MARKET_MIN_SIMILAR:
            prices, reference = similar, "похожим квартирам"
        elif len(same_room) >= MARKET_MIN_ROOM_GROUP:
            prices, reference = [price for _, price in same_room], f"{location_label} и комнатности"
        elif len(all_local_prices) >= MARKET_MIN_DISTRICT_GROUP:
            prices, reference = all_local_prices, location_label
        else:
            continue
        listing.market_samples = len(prices)
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
    page_listings = list(listings.order_by(sortings[ordering], "-id")[:200])
    return render(request, "listings/feed.html", {
        "listings": add_market_position(page_listings),
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
    page_listings = list(listings.order_by(sortings[ordering], "-id")[:200])
    return render(request, "listings/feed.html", {
        "listings": add_market_position(page_listings),
        "result_count": listings.count(), "filters": filters, "filter_options": filter_options(visible=False),
        "sort": ordering, "hidden_feed": True,
    })


@login_required
def listing_detail(request, listing_id: int):
    listing = get_object_or_404(Listing, pk=listing_id)
    add_market_position([listing])
    price_history = list(listing.price_history.exclude(price__isnull=True).order_by("observed_at", "id"))
    chart_points = ""
    if price_history:
        prices = [item.price for item in price_history]
        low, high = min(prices), max(prices)
        spread = high - low or 1
        chart_points = " ".join(
            f"{round(index / max(len(price_history) - 1, 1) * 680 + 10, 1)},"
            f"{round(170 - (item.price - low) / spread * 140, 1)}"
            for index, item in enumerate(price_history)
        )
    snapshots = list(listing.snapshots.order_by("-observed_at", "-id")[:12])
    return render(request, "listings/detail.html", {
        "item": listing, "price_history": price_history, "chart_points": chart_points,
        "price_low": min((entry.price for entry in price_history), default=None),
        "price_high": max((entry.price for entry in price_history), default=None),
        "snapshots": snapshots,
    })


@login_required
def scan_statistics(request):
    if request.method == "POST":
        if request.POST.get("action") == "manual_domclick":
            active_job = ManualDomclickJob.objects.filter(
                status__in=[ManualDomclickJob.Status.QUEUED, ManualDomclickJob.Status.RUNNING],
            ).first()
            if not active_job:
                search = SearchQuery.objects.filter(source=SearchQuery.Source.DOMCLICK, enabled=True).first()
                if search:
                    ManualDomclickJob.objects.create(search_query=search)
            return redirect("scan_statistics")
        source = request.POST.get("source")
        mode = request.POST.get("mode")
        enabled = request.POST.get("enabled")
        if (source in SearchQuery.Source.values and mode in Scan.Mode.values and enabled in {"0", "1"}
                and not polling_mode_locked(source, mode)):
            SourcePollingControl.objects.update_or_create(
                source=source, mode=mode, defaults={"enabled": enabled == "1"},
            )
        return redirect("scan_statistics")

    sources = []
    now = timezone.now()
    controls = {(control.source, control.mode): control.enabled for control in SourcePollingControl.objects.all()}
    for value, label in SearchQuery.Source.choices:
        recent_scans = list(Scan.objects.filter(source=value).select_related("search_query")
                            .order_by("-started_at", "-id")[:20])
        last_success = next((scan for scan in recent_scans if scan.status == Scan.Status.SUCCESS), None)
        failed_in_a_row = 0
        for scan in recent_scans:
            if scan.status != Scan.Status.FAILED:
                break
            failed_in_a_row += 1
        fast_enabled = controls.get((value, Scan.Mode.FAST), default_polling_enabled(value, Scan.Mode.FAST))
        if not fast_enabled:
            health, health_label, health_reason = "disabled", "Отключено", "Опросы отключены в интерфейсе."
        elif not recent_scans:
            health, health_label, health_reason = "unknown", "Нет данных", "Опросы ещё не запускались."
        elif recent_scans[0].status == Scan.Status.RUNNING:
            health, health_label, health_reason = "running", "Выполняется", "Сейчас идёт опрос площадки."
        elif failed_in_a_row >= 3:
            health, health_label = "error", "Ошибка"
            health_reason = f"{failed_in_a_row} ошибки подряд."
        elif recent_scans[0].status == Scan.Status.FAILED:
            health, health_label, health_reason = "warning", "Внимание", "Последний опрос завершился ошибкой."
        elif not last_success or not last_success.finished_at or last_success.finished_at < now - SCAN_STALE_AFTER:
            health, health_label, health_reason = "warning", "Данные устарели", "Нет успешного опроса за последние 6 часов."
        else:
            health, health_label, health_reason = "healthy", "В норме", "Последний опрос завершился успешно."
        next_runs = [
            {"mode": mode, "label": run_label, "scheduled_at": scheduled_at, "delay": delay,
             "enabled": controls.get((value, mode), default_polling_enabled(value, mode)),
             "locked": polling_mode_locked(value, mode)}
            for mode, run_label, scheduled_at, delay in next_poll_runs(value, now)
        ]
        sources.append({
            "value": value, "label": label, "fast_enabled": fast_enabled,
            "next_runs": next_runs,
            "scans": recent_scans[:3], "last_success": last_success,
            "failed_in_a_row": failed_in_a_row, "health": health,
            "health_label": health_label, "health_reason": health_reason,
        })
    manual_domclick_job = ManualDomclickJob.objects.select_related("scan").order_by("-id").first()
    return render(request, "listings/scan_statistics.html", {
        "sources": sources, "manual_domclick_job": manual_domclick_job,
    })


@login_required
def listing_map(request):
    listings, filters = filtered_listings(request)
    map_listings = list(listings.exclude(latitude__isnull=True).exclude(longitude__isnull=True)
                        .order_by("-first_seen_at")[:500])
    add_market_position(map_listings)
    return render(request, "listings/map.html", {
        "listings": map_listings, "result_count": len(map_listings), "filters": filters,
        "filter_options": filter_options(),
    })


@login_required
def dashboard(request):
    listings, filters = filtered_listings(request)
    period_days = _integer(request.GET.get("stats_days", 30))
    if period_days not in (7, 30, 90):
        period_days = 30
    period_since = timezone.now() - timedelta(days=period_days - 1)
    prices = list(listings.exclude(price__isnull=True).values_list("price", flat=True))
    sqm_prices = list(listings.exclude(price_per_sqm__isnull=True).values_list("price_per_sqm", flat=True))
    daily_raw = (listings.filter(first_seen_at__gte=period_since).annotate(day=TruncDate("first_seen_at"))
                 .values("day").annotate(count=Count("id")).order_by("day"))
    by_day = {row["day"]: row["count"] for row in daily_raw}
    daily = []
    for offset in range(period_days):
        day = (period_since + timedelta(days=offset)).date()
        daily.append({"label": day.strftime("%d.%m"), "count": by_day.get(day, 0)})
    district_stats = list(listings.exclude(district__isnull=True).exclude(district="")
                          .exclude(price_per_sqm__isnull=True).values("district")
                          .annotate(count=Count("id"), average=Avg("price_per_sqm"),
                                    average_area=Avg("area"),
                                    new_count=Count("id", filter=Q(first_seen_at__gte=period_since)))
                          .order_by("-count", "district")[:8])
    price_history = (PriceHistory.objects.filter(listing__in=listings, observed_at__gte=period_since,
                                                  price__isnull=False)
                     .select_related("listing").order_by("listing_id", "observed_at", "id"))
    price_changes = {}
    for observation in price_history:
        change = price_changes.setdefault(observation.listing_id, {"listing": observation.listing,
                                                                     "first": observation.price,
                                                                     "last": observation.price})
        change["last"] = observation.price
    changes = [
        {**change, "difference": change["last"] - change["first"]}
        for change in price_changes.values() if change["last"] != change["first"]
    ]
    price_drops = sorted((change for change in changes if change["difference"] < 0),
                         key=lambda change: change["difference"])[:10]
    scatter = list(listings.exclude(area__isnull=True).exclude(price__isnull=True)
                   .order_by("-first_seen_at").values("area", "price")[:150])
    return render(request, "listings/dashboard.html", {
        "filters": filters, "filter_options": filter_options(), "total": listings.count(),
        "median_price": int(median(prices)) if prices else None,
        "median_sqm_price": int(median(sqm_prices)) if sqm_prices else None,
        "period_days": period_days, "daily": daily,
        "daily_max": max((item["count"] for item in daily), default=1) or 1,
        "district_stats": district_stats, "scatter": scatter,
        "price_drop_count": len(price_drops),
        "price_increase_count": sum(change["difference"] > 0 for change in changes),
        "price_drops": price_drops,
    })
