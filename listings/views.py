from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone

from .models import (District, Listing, ListingSnapshot, ManualDomclickJob, Microdistrict, PriceHistory, Scan,
                     SearchQuery, SourcePollingControl, StreetAssignment)
from .services.enrichment import location_is_visible, parse_address
from .services.change_history import change_events
from .services.polling import default_polling_enabled


SCAN_STALE_AFTER = timedelta(hours=6)
SCHEDULE_TIME_ZONE = ZoneInfo("Europe/Moscow")
DISPLAY_TIME_ZONE = ZoneInfo("Asia/Yekaterinburg")
MARKET_MIN_SIMILAR = 5
MARKET_MIN_ROOM_GROUP = 8
MARKET_MIN_DISTRICT_GROUP = 12
MICRODISTRICT_MARKET_MIN_SAMPLE = 5


def _next_at(now, hour: int, minute: int):
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _next_hourly_at(now, minute: int):
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(hours=1)


def _next_avito_fast_at(now):
    """Return the next 90-minute Avito fast-scan systemd calendar slot."""
    candidates = []
    for day_offset in range(2):
        day = now.date() + timedelta(days=day_offset)
        for hour in range(0, 24, 3):
            candidates.append(now.replace(year=day.year, month=day.month, day=day.day,
                                          hour=hour, minute=15, second=0, microsecond=0))
            candidates.append(now.replace(year=day.year, month=day.month, day=day.day,
                                          hour=hour + 1, minute=45, second=0, microsecond=0))
    return next(candidate for candidate in candidates if candidate > now)


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
        runs = [(Scan.Mode.FAST, "Быстрый", _next_avito_fast_at(now), "в течение 10 мин."),
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


def similar_listings(listing: Listing, limit: int = 6):
    """Return local alternatives with the same room count and a close area."""
    if not listing.district or listing.rooms is None or listing.area is None:
        return []
    candidates = (Listing.objects.filter(is_visible=True, is_active=True, district=listing.district,
                                         rooms=listing.rooms)
                  .exclude(pk=listing.pk).exclude(area__isnull=True))
    if listing.microdistrict:
        candidates = candidates.filter(microdistrict=listing.microdistrict)
    else:
        candidates = candidates.filter(Q(microdistrict__isnull=True) | Q(microdistrict=""))
    area = float(listing.area)
    candidates = list(candidates.filter(area__gte=area - 10, area__lte=area + 10)[:150])
    target_sqm_price = float(listing.price_per_sqm) if listing.price_per_sqm else None
    candidates.sort(key=lambda item: (
        abs(float(item.area) - area),
        abs(float(item.price_per_sqm) - target_sqm_price)
        if target_sqm_price and item.price_per_sqm else float("inf"),
        -item.first_seen_at.timestamp(),
    ))
    return candidates[:limit]


def microdistrict_market_stats(listings, period_since, filters):
    """A median-based local market view; small groups are intentionally omitted."""
    rows = (listings.exclude(district__isnull=True).exclude(district="")
            .exclude(microdistrict__isnull=True).exclude(microdistrict="")
            .exclude(price_per_sqm__isnull=True)
            .values("district", "microdistrict", "price_per_sqm", "price", "area", "first_seen_at"))
    groups = {}
    for row in rows:
        key = (row["district"], row["microdistrict"])
        groups.setdefault(key, []).append(row)
    all_sqm_prices = [row["price_per_sqm"] for rows in groups.values() for row in rows]
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
        key = (snapshot.data.get("district") or snapshot.listing.district,
               snapshot.data.get("microdistrict") or snapshot.listing.microdistrict)
        if key in groups:
            removed_counts[key] = removed_counts.get(key, 0) + 1

    stats = []
    for (district, microdistrict), entries in groups.items():
        if len(entries) < MICRODISTRICT_MARKET_MIN_SAMPLE:
            continue
        sqm_prices = [entry["price_per_sqm"] for entry in entries]
        prices = [entry["price"] for entry in entries if entry["price"] is not None]
        areas = [float(entry["area"]) for entry in entries if entry["area"] is not None]
        median_sqm = int(median(sqm_prices))
        stats.append({
            "district": district, "microdistrict": microdistrict, "count": len(entries),
            "median_sqm": median_sqm, "median_price": int(median(prices)) if prices else None,
            "median_area": median(areas) if areas else None,
            "new_count": sum(entry["first_seen_at"] >= period_since for entry in entries),
            "removed_count": removed_counts.get((district, microdistrict), 0),
            "relative_pct": round((median_sqm / overall_median - 1) * 100) if overall_median else 0,
        })
    return sorted(stats, key=lambda row: (-row["median_sqm"], -row["count"], row["microdistrict"]))


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
def shortlist(request):
    listings, filters = filtered_listings(request)
    if not request.GET.get("days"):
        listings = listings.filter(first_seen_at__gte=timezone.now() - timedelta(days=7))
        filters["days"] = "7"
    recent = list(listings.order_by("-first_seen_at", "-id")[:500])
    add_market_position(recent)
    best = [item for item in recent if item.market_state == "below"]
    best.sort(key=lambda item: (item.market_delta_pct, -item.first_seen_at.timestamp()))
    return render(request, "listings/feed.html", {
        "listings": best, "result_count": len(best), "filters": filters,
        "filter_options": filter_options(), "sort": "new", "shortlist": True,
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
    snapshots = list(listing.snapshots.order_by("-observed_at", "-id")[:50])
    return render(request, "listings/detail.html", {
        "item": listing, "price_history": price_history, "chart_points": chart_points,
        "price_low": min((entry.price for entry in price_history), default=None),
        "price_high": max((entry.price for entry in price_history), default=None),
        "change_events": change_events(snapshots), "similar_listings": similar_listings(listing),
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
def data_quality(request):
    issue = request.GET.get("issue", "all")
    if request.method == "POST":
        issue = request.POST.get("issue", issue)
    issue_filters = {
        "address": Q(address__isnull=True) | Q(address=""),
        "district": Q(district_ref__isnull=True),
        "microdistrict": Q(microdistrict_ref__isnull=True),
    }
    if issue not in {"all", *issue_filters}:
        issue = "all"
    if request.method == "POST":
        item = get_object_or_404(Listing, pk=request.POST.get("listing_id"))
        address = (request.POST.get("address") or "").strip() or None
        district = District.objects.filter(pk=request.POST.get("district_id")).first()
        microdistrict = Microdistrict.objects.select_related("district").filter(
            pk=request.POST.get("microdistrict_id"),
        ).first()
        if microdistrict and (not district or microdistrict.district_id == district.id):
            district = microdistrict.district
        elif microdistrict:
            microdistrict = None
        parsed = parse_address(address, district.name if district else None)
        normalized_microdistrict = microdistrict.name if microdistrict else parsed.microdistrict
        normalized_district = district.name if district else parsed.district
        changed_location = (item.address, item.district, item.microdistrict) != (
            parsed.address, normalized_district, normalized_microdistrict,
        )
        item.address = item.address_override = parsed.address
        item.district = item.district_override = normalized_district
        item.microdistrict = item.microdistrict_override = normalized_microdistrict
        item.district_ref = district
        item.microdistrict_ref = microdistrict
        item.location_source = "manual"
        item.is_visible = location_is_visible(item.address, item.district, item.microdistrict)
        update_fields = ["address", "district", "microdistrict", "address_override", "district_override",
                         "microdistrict_override", "district_ref", "microdistrict_ref", "location_source",
                         "is_visible"]
        if changed_location:
            item.latitude = item.longitude = None
            item.geocode_status = item.geocoded_at = None
            update_fields += ["latitude", "longitude", "geocode_status", "geocoded_at"]
        item.save(update_fields=update_fields)
        return redirect(f"{reverse('data_quality')}?issue={issue}")

    missing = Q()
    for condition in issue_filters.values():
        missing |= condition
    selected = missing if issue == "all" else issue_filters[issue]
    visible_listings = Listing.objects.filter(is_visible=True)
    listings = list(visible_listings.filter(selected).order_by("-first_seen_at", "-id")[:100])
    for item in listings:
        item.quality_issues = [
            label for missing, label in ((not item.address, "Нет адреса"), (not item.district_ref, "Нет района"),
                                         (not item.microdistrict_ref, "Нет микрорайона"))
            if missing
        ]
    counts = {name: visible_listings.filter(condition).count() for name, condition in issue_filters.items()}
    counts["all"] = visible_listings.filter(missing).count()
    return render(request, "listings/data_quality.html", {
        "listings": listings, "issue": issue, "counts": counts,
        "districts": District.objects.all(),
        "microdistricts": Microdistrict.objects.select_related("district").all(),
    })


@login_required
def location_directory(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "microdistrict":
            district = get_object_or_404(District, pk=request.POST.get("district_id"))
            name = (request.POST.get("name") or "").strip()
            if name:
                Microdistrict.objects.get_or_create(district=district, name=name)
        elif action == "street":
            district = get_object_or_404(District, pk=request.POST.get("district_id"))
            microdistrict = Microdistrict.objects.filter(pk=request.POST.get("microdistrict_id")).first()
            if microdistrict and microdistrict.district_id != district.id:
                microdistrict = None
            street = (request.POST.get("street") or "").strip()
            if street:
                StreetAssignment.objects.create(
                    street=street,
                    house_from=_integer(request.POST.get("house_from")),
                    house_to=_integer(request.POST.get("house_to")),
                    parity=request.POST.get("parity") or StreetAssignment.Parity.ANY,
                    district=district, microdistrict=microdistrict,
                    note=(request.POST.get("note") or "").strip(),
                )
        return redirect("location_directory")
    return render(request, "listings/location_directory.html", {
        "districts": District.objects.prefetch_related("microdistricts").all(),
        "microdistricts": Microdistrict.objects.select_related("district").all(),
        "street_assignments": StreetAssignment.objects.select_related("district", "microdistrict").all(),
        "parities": StreetAssignment.Parity.choices,
    })


@login_required
def listing_map(request):
    listings, filters = filtered_listings(request)
    map_listings = list(listings.exclude(latitude__isnull=True).exclude(longitude__isnull=True)
                        .order_by("-first_seen_at")[:500])
    selected_listing_id = _integer(request.GET.get("listing"))
    selected_listing = None
    if selected_listing_id:
        selected_listing = (Listing.objects.filter(pk=selected_listing_id)
                            .exclude(latitude__isnull=True).exclude(longitude__isnull=True).first())
        if selected_listing and all(item.pk != selected_listing.pk for item in map_listings):
            map_listings.append(selected_listing)
    add_market_position(map_listings)
    return render(request, "listings/map.html", {
        "listings": map_listings, "result_count": len(map_listings), "filters": filters,
        "filter_options": filter_options(), "selected_listing_id": selected_listing.pk if selected_listing else None,
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
    microdistrict_stats = microdistrict_market_stats(listings, period_since, filters)
    return render(request, "listings/dashboard.html", {
        "filters": filters, "filter_options": filter_options(), "total": listings.count(),
        "median_price": int(median(prices)) if prices else None,
        "median_sqm_price": int(median(sqm_prices)) if sqm_prices else None,
        "period_days": period_days, "daily": daily,
        "daily_max": max((item["count"] for item in daily), default=1) or 1,
        "district_stats": district_stats, "scatter": scatter,
        "microdistrict_stats": microdistrict_stats,
        "microdistrict_min_sample": MICRODISTRICT_MARKET_MIN_SAMPLE,
        "price_drop_count": len(price_drops),
        "price_increase_count": sum(change["difference"] > 0 for change in changes),
        "price_drops": price_drops,
    })
