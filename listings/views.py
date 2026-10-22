from collections import Counter, defaultdict
import hashlib
import json
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil
from statistics import median
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Avg, Count, F, Max, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone

from .models import (AvitoDetailPayload, AvitoDetailPollState, CianDetailPayload, CianDetailPollProgress, CianDetailPollState, CianFullScanCheckpoint, Consideration, District, GlobalListingHide,
                     Listing, ListingReview, ListingReviewRevision, ListingSearchQuery, ManualDomclickJob,
                     Microdistrict, NetworkRoute, PollingProfile, PriceHistory, ReviewTag, Scan, ScoringPreference, SearchQuery, SourcePollingControl,
                     StreetAssignment, UserListingHide, MicrodistrictBoundary, GeoZone, SourcePollingProfile, PollingDiagnosticJob)
from .services.enrichment import location_is_visible, parse_address
from .services.location_directory import apply_location_rules_for_assignments, location_is_visible_with_rules
from .services.microdistrict_polygons import load_polygons
from .services.change_history import change_events
from .services.polling import default_polling_enabled
from .services.scan_health import cian_detail_health
from .services.telegram_reviews import handle_update
from .services.personal_filters import annotate_personal_filter
from .services.database_backup import BackupError, create_database_backup
from .services.market import MIN_TARGET_AREA, MICRODISTRICT_MARKET_MIN_SAMPLE, annotate_market_position
from .services.market import microdistrict_market_stats as _service_microdistrict_market_stats
from .services.scoring import add_listing_score as _service_add_listing_score
from .services.scoring import scoring_preference_for as _service_scoring_preference_for
from .services.reviews import personal_candidate_queryset as _service_personal_candidate_queryset
from .services.listing_queries import (
    filtered_listings as _service_filtered_listings,
    filter_options as _service_filter_options,
    similar_listings as _service_similar_listings,
)
from .services.dashboard import (
    build_daily_series,
    build_district_stats,
    build_distribution,
    build_price_changes,
)


SCAN_STALE_AFTER = timedelta(hours=6)
SCHEDULE_TIME_ZONE = ZoneInfo("Europe/Moscow")
DISPLAY_TIME_ZONE = ZoneInfo("Asia/Yekaterinburg")


@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)
    handle_update(payload)
    return JsonResponse({"ok": True})
# Candidate/review threshold.  Smaller listings remain in the database and in
# the market-comparison pool, but are not shown as purchase candidates.


def _next_at(now, hour: int, minute: int):
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def _next_hourly_at(now, minute: int):
    candidate = now.replace(minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(hours=1)


def _next_avito_fast_at(now):
    """Return the next daytime 90-minute Avito fast-scan calendar slot."""
    candidates = []
    for day_offset in range(2):
        day = now.date() + timedelta(days=day_offset)
        for hour in range(8, 23):
            candidates.append(now.replace(year=day.year, month=day.month, day=day.day,
                                          hour=hour, minute=15, second=0, microsecond=0))
        for hour in range(9, 23):
            candidates.append(now.replace(year=day.year, month=day.month, day=day.day,
                                          hour=hour, minute=45, second=0, microsecond=0))
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
        if value in (None, ""):
            return None
        # Browsers and keyboards in the Russian locale commonly submit a
        # decimal comma.  Decimal only accepts a dot, so normalise it before
        # saving a user-entered area filter.
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def add_market_position(listings):
    return annotate_market_position(listings)


def scoring_preference_for(user):
    return _service_scoring_preference_for(user)


def add_listing_score(listings, preferences=None):
    """Backward-compatible view import; scoring lives in the service layer."""
    return _service_add_listing_score(listings, preferences)


def personal_candidate_queryset(queryset, user):
    """Backward-compatible view import; candidate filtering lives in a service."""
    return _service_personal_candidate_queryset(queryset, user)


def filtered_listings(request, *, visible: bool | None = True):
    """Вызывает сервис фильтрации для совместимости view-импортов."""
    return _service_filtered_listings(request, visible=visible)


def filter_options(*, visible: bool = True):
    """Возвращает варианты фильтров через сервис запросов."""
    return _service_filter_options(visible=visible)


def similar_listings(listing: Listing, limit: int = 6):
    """Возвращает локальные альтернативы через сервис запросов."""
    return _service_similar_listings(listing, limit)


def microdistrict_market_stats(listings, period_since, filters):
    """Возвращает рыночную статистику через сервис market analytics."""
    return _service_microdistrict_market_stats(listings, period_since, filters)


@login_required
def listing_feed(request):
    listings, filters = filtered_listings(request)
    # Keep sub-55 m² records available for market statistics/comparables, but
    # do not present them as purchase candidates in the main feed.
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    preferences = scoring_preference_for(request.user)
    listings = personal_candidate_queryset(listings, request.user)
    ordering = request.GET.get("sort", "new")
    sortings = {
        "new": "-first_seen_at", "price_up": "price", "price_down": "-price",
        "sqm_up": "price_per_sqm", "area_down": "-area", "score": "-id",
    }
    if ordering not in sortings:
        ordering = "new"
    if isinstance(listings, list):
        candidates = listings
        candidate_count = len(candidates)
        candidates.sort(key=lambda item: (item.first_seen_at, item.id), reverse=True)
    else:
        candidate_count = listings.count()
        candidates = list(listings.order_by("-first_seen_at", "-id")[:500])
    if ordering == "score":
        page_listings = candidates[:500]
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
        page_listings.sort(key=lambda item: (-item.score, -item.first_seen_at.timestamp()))
        page_listings = page_listings[:200]
    else:
        page_listings = candidates
        if ordering == "price_up":
            page_listings.sort(key=lambda item: (item.price is None, item.price or 0, -item.id))
        elif ordering == "price_down":
            page_listings.sort(key=lambda item: (item.price is not None, item.price or 0, item.id), reverse=True)
        elif ordering == "sqm_up":
            page_listings.sort(key=lambda item: (item.price_per_sqm is None, item.price_per_sqm or 0, -item.id))
        elif ordering == "area_down":
            page_listings.sort(key=lambda item: (item.area is not None, item.area or 0, item.id), reverse=True)
        page_listings = page_listings[:200]
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
    # Keep the personal review visible alongside the automatic score.  Reviews
    # are private to their author, so fetch only the current user's rows.
    reviews_by_listing = {
        review.listing_id: review
        for review in ListingReview.objects.filter(author=request.user, listing_id__in=[item.id for item in page_listings])
    }
    for item in page_listings:
        item.my_review = reviews_by_listing.get(item.id)
        item.review_state = ("consider" if item.my_review and item.my_review.decision == ListingReview.Decision.CONSIDER
                             else "reject" if item.my_review else "pending")
    return render(request, "listings/feed.html", {
        "listings": page_listings,
        "result_count": candidate_count, "filters": filters, "filter_options": filter_options(),
        "sort": ordering,
    })


@login_required
def shortlist(request):
    listings, filters = filtered_listings(request)
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    if not request.GET.get("days"):
        listings = listings.filter(first_seen_at__gte=timezone.now() - timedelta(days=7))
        filters["days"] = "7"
    listings = personal_candidate_queryset(listings, request.user)
    recent = (listings if isinstance(listings, list) else list(listings.order_by("-first_seen_at", "-id")[:500]))
    add_market_position(recent)
    add_listing_score(recent, scoring_preference_for(request.user))
    best = [item for item in recent if item.market_state == "below"]
    best.sort(key=lambda item: (item.market_delta_pct, -item.first_seen_at.timestamp()))
    reviews_by_listing = {
        review.listing_id: review
        for review in ListingReview.objects.filter(author=request.user, listing_id__in=[item.id for item in best])
    }
    for item in best:
        item.my_review = reviews_by_listing.get(item.id)
    return render(request, "listings/feed.html", {
        "listings": best, "result_count": len(best), "filters": filters,
        "filter_options": filter_options(), "sort": "new", "shortlist": True,
    })


def review_queue(request):
    from .views_reviews import review_queue as _review_queue_view
    return _review_queue_view(request)


def consideration_list(request):
    from .views_reviews import consideration_list as _consideration_list_view
    return _consideration_list_view(request)


def my_reviews(request):
    from .views_reviews import my_reviews as _my_reviews_view
    return _my_reviews_view(request)


@login_required
def review_export(request, format):
    reviews = ListingReview.objects.filter(author=request.user).select_related("listing").prefetch_related("tags")
    rows = [{"listing_id": review.listing_id, "url": review.listing.url, "title": review.listing.title,
             "source": review.listing.source, "price": review.listing.price, "rooms": review.listing.rooms,
             "area": str(review.listing.area or ""), "rating": review.rating, "decision": review.decision,
             "tags": [tag.code for tag in review.tags.all()], "comment": review.comment,
             "interest_reason": review.interest_reason, "deal_breaker": review.deal_breaker,
             "created_at": review.created_at.isoformat(), "updated_at": review.updated_at.isoformat()} for review in reviews]
    if format == "json":
        return JsonResponse(rows, safe=False, json_dumps_params={"ensure_ascii": False, "indent": 2})
    if format != "csv":
        return HttpResponse(status=404)
    import csv
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="home-hunter-reviews.csv"'
    response.write("\ufeff")
    writer = csv.DictWriter(response, fieldnames=rows[0].keys() if rows else ("listing_id", "rating", "decision"))
    writer.writeheader()
    for row in rows:
        row["tags"] = ", ".join(row["tags"])
        writer.writerow(row)
    return response


@login_required
def market_export_xlsx(request):
    from .services.market_export import build_market_xlsx

    response = HttpResponse(
        build_market_xlsx(request.user),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="home-hunter-market-export.xlsx"'
    return response


@staff_member_required
def database_backup(request):
    if request.method == "GET":
        return render(request, "listings/database_backup.html")
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        file_obj, filename, size = create_database_backup()
    except BackupError as exc:
        return render(request, "listings/database_backup.html", {"error": str(exc)}, status=503)
    response = FileResponse(
        file_obj, as_attachment=True, filename=filename,
        content_type="application/octet-stream",
    )
    response["Content-Length"] = str(size)
    return response


@login_required
def hidden_listing_feed(request):
    listings, filters = filtered_listings(request, visible=False)
    preferences = scoring_preference_for(request.user)
    ordering = request.GET.get("sort", "new")
    sortings = {
        "new": "-first_seen_at", "price_up": "price", "price_down": "-price",
        "sqm_up": "price_per_sqm", "area_down": "-area", "score": "-id",
    }
    if ordering not in sortings:
        ordering = "new"
    if ordering == "score":
        page_listings = list(listings.order_by("-first_seen_at", "-id")[:500])
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
        page_listings.sort(key=lambda item: (-item.score, -item.first_seen_at.timestamp()))
        page_listings = page_listings[:200]
    else:
        page_listings = list(listings.order_by(sortings[ordering], "-id")[:200])
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
    return render(request, "listings/feed.html", {
        "listings": page_listings,
        "result_count": listings.count(), "filters": filters, "filter_options": filter_options(visible=False),
        "sort": ordering, "hidden_feed": True,
    })


@login_required
def listing_detail(request, listing_id: int):
    # Sub-55 m² records remain available for market statistics, but are not
    # candidate cards and must not be reachable as a purchase/review card.
    listing = get_object_or_404(Listing, pk=listing_id, area__gte=MIN_TARGET_AREA)
    annotate_personal_filter([listing], request.user)
    own_review = ListingReview.objects.filter(listing=listing, author=request.user).first()
    peer_reviews = (ListingReview.objects.filter(listing=listing).exclude(author=request.user)
                    .select_related("author").prefetch_related("tags")) if own_review else []
    if listing.source == SearchQuery.Source.AVITO:
        try:
            detail_photos = listing.avito_detail_state.detail_data.get("photos", [])
            detail_state = listing.avito_detail_state
        except AvitoDetailPollState.DoesNotExist:
            detail_photos, detail_state = [], None
    else:
        try:
            detail_photos = listing.cian_detail_state.detail_data.get("photos", [])
            detail_state = listing.cian_detail_state
        except CianDetailPollState.DoesNotExist:
            detail_photos, detail_state = [], None
    # A deactivated CIAN offer clears the poll state's live photo list, but
    # the last successful detail payload still contains the complete gallery.
    # Keep those archived photos available on the listing detail page.
    if not detail_photos:
        if listing.source == SearchQuery.Source.CIAN:
            try:
                archived_photos = (listing.cian_detail_payload.payload.get("offer", {}).get("photos", []))
            except CianDetailPayload.DoesNotExist:
                archived_photos = []
            detail_photos = [{"full_url": photo.get("fullUrl") or photo.get("full_url")} for photo in archived_photos if photo.get("fullUrl") or photo.get("full_url")]
        elif listing.source == SearchQuery.Source.AVITO:
            try:
                detail_photos = listing.avito_detail_payload.payload.get("photos", [])
            except AvitoDetailPayload.DoesNotExist:
                detail_photos = []
    add_market_position([listing])
    add_listing_score([listing], scoring_preference_for(request.user))
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
    yes_no = lambda value: "Да" if value else "Нет"
    repair_types = {
        "cosmetic": "Косметический",
        "euro": "Евроремонт",
        "withoutRepair": "Без ремонта",
    }
    windows_view_types = {
        "yard": "Во двор",
        "street": "На улицу",
        "yardAndStreet": "Во двор и на улицу",
    }
    building_material_types = {
        "brick": "Кирпичный",
        "monolith": "Монолитный",
        "panel": "Панельный",
    }
    parking_types = {"ground": "Наземная", "underground": "Подземная"}
    bathroom_parts = []
    if listing.bathrooms_combined:
        bathroom_parts.append(f"совмещённый — {listing.bathrooms_combined}")
    if listing.bathrooms_separate:
        bathroom_parts.append(f"раздельный — {listing.bathrooms_separate}")
    lift_parts = []
    if listing.passenger_lifts_count:
        lift_parts.append(f"пассажирских — {listing.passenger_lifts_count}")
    if listing.cargo_lifts_count:
        lift_parts.append(f"грузовых — {listing.cargo_lifts_count}")
    apartment_attributes = [
        ("Площадь кухни", f"{listing.kitchen_area} м²" if listing.kitchen_area else None),
        ("Жилая площадь", f"{listing.living_area} м²" if listing.living_area else None),
        ("Высота потолков", f"{listing.ceiling_height} м" if listing.ceiling_height else None),
        ("Санузел", ", ".join(bathroom_parts) or None),
        ("Балконы", listing.balconies_count), ("Лоджии", listing.loggias_count),
        ("Ремонт", repair_types.get(listing.repair_type, listing.repair_type)),
        ("Вид из окон", windows_view_types.get(listing.windows_view_type, listing.windows_view_type)),
        ("С мебелью", yes_no(listing.has_furniture) if listing.has_furniture is not None else None),
    ]
    building_attributes = [
        ("Тип дома", building_material_types.get(listing.building_material_type, listing.building_material_type)),
        ("Количество лифтов", ", ".join(lift_parts) or None),
        ("Парковка", parking_types.get(listing.parking_type, listing.parking_type)),
        ("Пандус", yes_no(listing.has_ramp) if listing.has_ramp is not None else None),
        ("Мусоропровод", yes_no(listing.has_garbage_chute) if listing.has_garbage_chute is not None else None),
    ]
    return render(request, "listings/detail.html", {
        "item": listing, "price_history": price_history, "chart_points": chart_points,
        "price_low": min((entry.price for entry in price_history), default=None),
        "price_high": max((entry.price for entry in price_history), default=None),
        "change_events": change_events(snapshots), "similar_listings": similar_listings(listing),
        "detail_photos": detail_photos,
        "detail_state": detail_state,
        "apartment_attributes": [(label, value) for label, value in apartment_attributes if value is not None],
        "building_attributes": [(label, value) for label, value in building_attributes if value is not None],
        "own_review": own_review, "peer_reviews": peer_reviews,
    })


@staff_member_required
def polling_profiles(request):
    """Assign reusable network profiles without exposing proxy secrets."""
    action = request.POST.get("action") if request.method == "POST" else ""
    if request.method == "POST" and action == "test_profile":
        source = request.POST.get("source", "")
        profile_id = request.POST.get("profile_id", "")
        profile = PollingProfile.objects.filter(
            pk=profile_id, source=source, mode="all", enabled=True, route__enabled=True,
        ).first()
        search = SearchQuery.objects.filter(source=source, enabled=True).order_by("pk").first()
        active_job = PollingDiagnosticJob.objects.filter(
            source=source, status__in=[PollingDiagnosticJob.Status.QUEUED, PollingDiagnosticJob.Status.RUNNING],
        ).first()
        if profile is None:
            messages.error(request, "Профиль не найден или отключён.")
        elif search is None:
            messages.error(request, "Для площадки нет включённого поискового запроса.")
        elif active_job:
            messages.warning(request, "Для этой площадки уже выполняется диагностический запуск.")
        else:
            PollingDiagnosticJob.objects.create(
                source=source, search_query=search, profile=profile, mode=Scan.Mode.FAST,
            )
            messages.success(request, f"Тест профиля «{profile.name}» поставлен в очередь.")
        return redirect("polling_profiles")

    if request.method == "POST" and action in {"assign_profile", "assign_source_profile"}:
        source = request.POST.get("source", "")
        profile_id = request.POST.get("profile_id", "")
        profile = PollingProfile.objects.filter(
            pk=profile_id, source=source, mode="all", enabled=True, route__enabled=True,
        ).first()
        if profile is None:
            messages.error(request, "Профиль не найден или отключён.")
        else:
            SourcePollingProfile.objects.update_or_create(
                source=source, defaults={"profile": profile, "enabled": True},
            )
            PollingProfile.objects.filter(source=source, mode="all").update(is_default=False)
            profile.is_default = True
            profile.save(update_fields=("is_default", "updated_at"))
            messages.success(request, f"Профиль «{profile.name}» назначен для площадки {source}.")
        return redirect("polling_profiles")

    profiles = list(PollingProfile.objects.select_related("route").filter(enabled=True).order_by("source", "mode", "name"))
    assignments = {
        assignment.source: assignment
        for assignment in SourcePollingProfile.objects.select_related("profile", "profile__route").filter(enabled=True)
    }
    grouped = []
    diagnostics = {}
    for job in PollingDiagnosticJob.objects.select_related("profile", "scan").filter(
        source__in=[source for source, _ in SearchQuery.Source.choices],
    )[:100]:
        diagnostics.setdefault(job.source, job)
    for source, source_label in SearchQuery.Source.choices:
        options = [item for item in profiles if item.source == source and item.mode == "all"]
        if options:
            assignment = assignments.get(source)
            selected = assignment.profile if assignment and assignment.profile in options else None
            grouped.append({
                "source": source, "source_label": source_label,
                "profiles": options, "selected": selected or next(
                    (item for item in options if item.is_default), options[0],
                ), "diagnostic": diagnostics.get(source),
            })
    return render(request, "listings/polling_profiles.html", {
        "routes": NetworkRoute.objects.prefetch_related("polling_profiles").order_by("name"),
        "profile_groups": grouped,
        "has_active_diagnostic_jobs": any(
            job.status in {PollingDiagnosticJob.Status.QUEUED, PollingDiagnosticJob.Status.RUNNING}
            for job in diagnostics.values()
        ),
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

    def scan_health(scans, enabled):
        last_success = next((scan for scan in scans if scan.status == Scan.Status.SUCCESS), None)
        failed_in_a_row = 0
        for scan in scans:
            if scan.status != Scan.Status.FAILED:
                break
            failed_in_a_row += 1
        if not enabled:
            return "disabled", "Отключено", "Опросы отключены в интерфейсе.", last_success, failed_in_a_row
        if not scans:
            return "unknown", "Нет данных", "Опросы ещё не запускались.", last_success, failed_in_a_row
        if scans[0].status == Scan.Status.RUNNING:
            return "running", "Выполняется", "Сейчас идёт опрос площадки.", last_success, failed_in_a_row
        if failed_in_a_row >= 3:
            return "error", "Ошибка", f"{failed_in_a_row} ошибки подряд.", last_success, failed_in_a_row
        if scans[0].status == Scan.Status.FAILED:
            return "warning", "Внимание", "Последний опрос завершился ошибкой.", last_success, failed_in_a_row
        if not last_success or not last_success.finished_at or last_success.finished_at < now - SCAN_STALE_AFTER:
            return "warning", "Данные устарели", "Нет успешного опроса за последние 6 часов.", last_success, failed_in_a_row
        return "healthy", "В норме", "Последний опрос завершился успешно.", last_success, failed_in_a_row

    sources = []
    now = timezone.now()
    controls = {(control.source, control.mode): control.enabled for control in SourcePollingControl.objects.all()}
    cian_checkpoints = {
        checkpoint.search_query_id: checkpoint
        for checkpoint in CianFullScanCheckpoint.objects.select_related("search_query")
    }
    cian_candidate_count = ListingSearchQuery.objects.filter(
        search_query__source=SearchQuery.Source.CIAN, missed_full_scans__gt=0,
    ).count()
    active_cian = Listing.objects.filter(source=SearchQuery.Source.CIAN, is_active=True)
    cian_active_ids = set(active_cian.values_list("pk", flat=True))
    cian_progress = CianDetailPollProgress.objects.select_related("current_listing").filter(source="cian").first()
    detail_health = cian_detail_health()
    detail_cycle = None
    if cian_progress:
        completed_ids = [pk for pk in cian_progress.completed_listing_ids if pk in cian_active_ids]
        # The active set may shrink during a detail pass.  Display its current
        # size, rather than a historical cycle total which can no longer be
        # reached after listings become inactive.
        total = len(cian_active_ids)
        completed = min(len(completed_ids), total)
        elapsed_hours = max((now - cian_progress.cycle_started_at).total_seconds() / 3600, 0)
        actual_rate = round(completed / elapsed_hours, 1) if completed and elapsed_hours >= 1 / 60 else None
        # Until a measurable rate exists, use the configured cadence: six
        # cards every quarter hour (24 cards/hour).
        forecast_rate = actual_rate or 24
        eta = now + timedelta(hours=max(total - completed, 0) / forecast_rate) if forecast_rate else None
        next_queue = list(active_cian.exclude(pk__in=completed_ids).exclude(
            pk=cian_progress.current_listing_id,
        ).select_related("cian_detail_state").order_by(
            F("cian_detail_state__last_checked_at").asc(nulls_first=True), "pk",
        )[:5])
        detail_cycle = {
            "total": total, "completed": completed, "remaining": max(total - completed, 0),
            "progress": min(100, round(100 * completed / total)) if total else 100,
            "started_at": cian_progress.cycle_started_at, "current": cian_progress.current_listing,
            "current_started_at": cian_progress.current_started_at, "next_queue": next_queue,
            "actual_rate": actual_rate, "eta": eta, "last_completed_at": cian_progress.last_completed_at,
        }
    for value, label in SearchQuery.Source.choices:
        recent_scans = list(Scan.objects.filter(source=value).select_related("search_query")
                            .order_by("-started_at", "-id")[:20])
        next_runs = [
            {"mode": mode, "label": run_label, "scheduled_at": scheduled_at, "delay": delay,
             "enabled": controls.get((value, mode), default_polling_enabled(value, mode)),
             "locked": polling_mode_locked(value, mode)}
            for mode, run_label, scheduled_at, delay in next_poll_runs(value, now)
        ]
        modes = []
        for run in next_runs:
            mode_scans = [scan for scan in recent_scans if scan.mode == run["mode"]]
            health, health_label, health_reason, last_success, failed_in_a_row = scan_health(
                mode_scans, run["enabled"],
            )
            modes.append({
                **run, "scans": mode_scans[:3], "health": health, "health_label": health_label,
                "health_reason": health_reason, "last_success": last_success,
                "failed_in_a_row": failed_in_a_row,
            })
        source_health = next(
            (health for health in ("running", "error", "warning", "disabled", "healthy", "unknown")
             if any(mode["health"] == health for mode in modes)),
            "unknown",
        )
        full_cycle = None
        if value == SearchQuery.Source.CIAN:
            checkpoint = next((cian_checkpoints.get(scan.search_query_id) for scan in recent_scans
                               if scan.mode == Scan.Mode.FULL and cian_checkpoints.get(scan.search_query_id)), None)
            if checkpoint is None:
                checkpoint = next(iter(cian_checkpoints.values()), None)
            if checkpoint:
                last_full = next((scan for scan in recent_scans if scan.mode == Scan.Mode.FULL), None)
                per_page = max(1, (last_full.items_seen // last_full.pages_scanned)
                               if last_full and last_full.pages_scanned else 28)
                estimated_pages = ceil(checkpoint.expected_total / per_page) if checkpoint.expected_total else None
                full_cycle = {
                    "search_name": checkpoint.search_query.name, "started_at": checkpoint.cycle_started_at,
                    "next_page": checkpoint.next_page, "expected_total": checkpoint.expected_total,
                    "collected_count": len(checkpoint.external_ids), "total_changes": checkpoint.total_changes,
                    "estimated_pages": estimated_pages,
                    "progress": min(100, round(100 * len(checkpoint.external_ids) / checkpoint.expected_total))
                    if checkpoint.expected_total else 0,
                }
        sources.append({
            "value": value, "label": label, "modes": modes, "health": source_health,
            "icon": {"avito": "A", "cian": "⌖", "domclick": "⌂"}[value],
            "items_seen": sum(scan.items_seen or 0 for mode in modes for scan in mode["scans"]),
            "new_items": sum(scan.new_items or 0 for mode in modes for scan in mode["scans"]),
            "successful_runs": sum(
                scan.status == Scan.Status.SUCCESS for mode in modes for scan in mode["scans"]
            ),
            "full_cycle": full_cycle, "candidate_count": cian_candidate_count if value == "cian" else 0,
            "detail_poll": ({
                "total": len(cian_active_ids),
                "checked": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, last_checked_at__isnull=False).count(),
                "published": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, status="published").count(),
                "errors": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN).exclude(last_error="").count(),
                "last": CianDetailPollState.objects.filter(listing__source=SearchQuery.Source.CIAN, last_checked_at__isnull=False).order_by("-last_checked_at").first(),
                "cycle": detail_cycle,
                "health": {"state": detail_health.state, "label": detail_health.label, "detail": detail_health.detail},
            } if value == "cian" else None),
        })
    manual_domclick_job = ManualDomclickJob.objects.select_related("scan").order_by("-id").first()
    return render(request, "listings/scan_statistics.html", {
        "sources": sources, "manual_domclick_job": manual_domclick_job,
    })


@login_required
def disappeared_listings(request):
    relations = (ListingSearchQuery.objects.filter(
        search_query__source=SearchQuery.Source.CIAN, missed_full_scans__gt=0,
    ).select_related("listing", "search_query").order_by("-missed_full_scans", "-last_seen_at"))
    return render(request, "listings/disappeared_listings.html", {
        "candidates": relations.filter(is_active=True),
        "disappeared": relations.filter(is_active=False),
    })


@login_required
def unavailable_cian_listings(request):
    """Archive of cards whose individual CIAN page confirmed removal."""
    unavailable = list(
        CianDetailPollState.objects.filter(status=CianDetailPollState.Status.UNAVAILABLE)
        .select_related("listing").order_by("-first_unavailable_at", "-last_checked_at")
    )
    listing_ids = [state.listing_id for state in unavailable]

    # The detail endpoint reports a technical state, not a reason for sale.
    # Keep the original value in the report while showing a human-readable label.
    status_labels = {
        "deactivated": "Снято с публикации",
        "deleted": "Удалено",
        "draft": "Черновик",
        "refused": "Отклонено ЦИАН",
        "unavailable": "Недоступно",
    }
    for state in unavailable:
        state.cian_status_value = (state.detail_data or {}).get("status") or "unavailable"
        state.cian_status_label = status_labels.get(
            state.cian_status_value,
            state.cian_status_value.replace("_", " ").capitalize(),
        )
    status_counts = Counter(
        state.cian_status_value for state in unavailable
    )
    total = len(unavailable)

    def average(values, digits=0):
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), digits) if values else None

    durations = [
        (state.first_unavailable_at - state.listing.first_seen_at).total_seconds() / 86400
        for state in unavailable
        if state.first_unavailable_at and state.listing.first_seen_at
    ]
    price_history = defaultdict(list)
    if listing_ids:
        for row in (PriceHistory.objects.filter(listing_id__in=listing_ids, price__isnull=False)
                    .order_by("listing_id", "observed_at", "id")
                    .values("listing_id", "price")):
            price_history[row["listing_id"]].append(row["price"])
    tracked_prices = [prices for prices in price_history.values() if len(prices) > 1]
    price_down_count = sum(prices[-1] < prices[0] for prices in tracked_prices)
    price_up_count = sum(prices[-1] > prices[0] for prices in tracked_prices)
    repair_labels = {
        "cosmetic": "Косметический",
        "euro": "Евроремонт",
        "design": "Дизайнерский",
        "no": "Без ремонта",
        "rough": "Черновой",
    }

    def grouped_rows(attribute, labels=None):
        groups = defaultdict(list)
        for state in unavailable:
            raw_value = getattr(state.listing, attribute) or "Не указан"
            value = (labels or {}).get(raw_value, raw_value)
            groups[value].append(state)
        rows = []
        for value, items in groups.items():
            rows.append({
                "name": value,
                "count": len(items),
                "share": round(100 * len(items) / total) if total else 0,
                "avg_price": average([item.listing.price for item in items]),
                "avg_area": average([item.listing.area for item in items], 1),
            })
        return sorted(rows, key=lambda row: (-row["count"], str(row["name"])))

    status_rows = [
        {
            "value": value,
            "label": status_labels.get(value, value.replace("_", " ").capitalize()),
            "count": count,
            "share": round(100 * count / total) if total else 0,
        }
        for value, count in status_counts.most_common()
    ]
    active_listings = list(
        Listing.objects.filter(source=SearchQuery.Source.CIAN, is_active=True)
        .only("price", "price_per_sqm", "area", "floor")
    )
    stats = {
        "total": total,
        "observed_total": Listing.objects.filter(source=SearchQuery.Source.CIAN).count(),
        "active_total": len(active_listings),
        "avg_price": average([state.listing.price for state in unavailable]),
        "avg_area": average([state.listing.area for state in unavailable], 1),
        "avg_price_per_sqm": average([state.listing.price_per_sqm for state in unavailable]),
        "avg_floor": average([state.listing.floor for state in unavailable]),
        "active_avg_price": average([listing.price for listing in active_listings]),
        "active_avg_area": average([listing.area for listing in active_listings], 1),
        "active_avg_price_per_sqm": average([listing.price_per_sqm for listing in active_listings]),
        "active_avg_floor": average([listing.floor for listing in active_listings]),
        "duration_avg": round(sum(durations) / len(durations), 1) if durations else None,
        "duration_median": round(median(durations), 1) if durations else None,
        "duration_min": round(min(durations), 1) if durations else None,
        "duration_max": round(max(durations), 1) if durations else None,
        "tracked_prices": len(tracked_prices),
        "price_down_count": price_down_count,
        "price_up_count": price_up_count,
        "status_rows": status_rows,
        "district_rows": grouped_rows("district"),
        "room_rows": grouped_rows("rooms"),
        "repair_rows": grouped_rows("repair_type", repair_labels),
    }
    return render(request, "listings/unavailable_cian_listings.html", {
        "unavailable": unavailable, "stats": stats,
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
        if microdistrict and (not district or microdistrict.district_links.filter(district_id=district.id).exists()):
            district = district or microdistrict.district
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
        item.is_visible = location_is_visible_with_rules(item.address, item.district, item.microdistrict)
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
        elif action in {"street", "street_edit"}:
            assignment = None
            if action == "street_edit":
                assignment = get_object_or_404(StreetAssignment, pk=request.POST.get("assignment_id"))
                old_assignment = StreetAssignment(
                    street=assignment.street, house_from=assignment.house_from, house_to=assignment.house_to,
                    parity=assignment.parity,
                )
            district = get_object_or_404(District, pk=request.POST.get("district_id"))
            microdistrict = Microdistrict.objects.filter(pk=request.POST.get("microdistrict_id")).first()
            if microdistrict and not (microdistrict.district_id == district.id or
                                      microdistrict.district_links.filter(district_id=district.id).exists()):
                microdistrict = None
            street = (request.POST.get("street") or "").strip()
            if street:
                values = dict(street=street, house_from=_integer(request.POST.get("house_from")),
                              house_to=_integer(request.POST.get("house_to")),
                              parity=request.POST.get("parity") or StreetAssignment.Parity.ANY,
                              district=district, microdistrict=microdistrict,
                              is_excluded=request.POST.get("is_excluded") == "1",
                              note=(request.POST.get("note") or "").strip())
                if assignment is None:
                    assignment, created = StreetAssignment.objects.get_or_create(
                        **{key: value for key, value in values.items() if key != "note"},
                        defaults={"note": values["note"]},
                    )
                    if not created:
                        assignment.note = values["note"]
                        assignment.save(update_fields=["note"])
                else:
                    for key, value in values.items():
                        setattr(assignment, key, value)
                    assignment.save()
                apply_location_rules_for_assignments(*(([old_assignment] if action == "street_edit" else [])), assignment)
        elif action == "street_delete":
            assignment = get_object_or_404(StreetAssignment, pk=request.POST.get("assignment_id"))
            old_assignment = StreetAssignment(street=assignment.street, house_from=assignment.house_from,
                                              house_to=assignment.house_to, parity=assignment.parity)
            assignment.delete()
            apply_location_rules_for_assignments(old_assignment)
        return redirect("location_directory")
    return render(request, "listings/location_directory.html", {
        "districts": District.objects.prefetch_related("microdistricts").all(),
        "microdistricts": Microdistrict.objects.select_related("district").all(),
        "street_assignments": StreetAssignment.objects.select_related("district", "microdistrict").all(),
        "parities": StreetAssignment.Parity.choices,
    })


@login_required
def scoring_settings(request):
    preference, _ = ScoringPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        preference.min_area = _decimal(request.POST.get("min_area"))
        preference.max_area = _decimal(request.POST.get("max_area"))
        preference.min_kitchen_area = _decimal(request.POST.get("min_kitchen_area"))
        preference.max_price = _integer(request.POST.get("max_price"))
        preference.preferred_rooms = [value for value in request.POST.getlist("preferred_rooms") if value.isdigit()]
        preference.floor_min = _integer(request.POST.get("floor_min"))
        preference.floor_max = _integer(request.POST.get("floor_max"))
        if preference.floor_min and preference.floor_max and preference.floor_min > preference.floor_max:
            preference.floor_min, preference.floor_max = preference.floor_max, preference.floor_min
        preference.preferred_districts = request.POST.getlist("preferred_districts")
        preference.preferred_repair_types = request.POST.getlist("preferred_repair_types")
        preference.prefer_photo = request.POST.get("prefer_photo") == "1"
        preference.require_lift = request.POST.get("require_lift") == "1"
        preference.require_balcony = request.POST.get("require_balcony") == "1"
        preference.prefer_furniture = request.POST.get("prefer_furniture") == "1"
        for field in ("market_weight", "freshness_weight", "data_weight", "floor_weight",
                      "price_history_weight", "preference_weight"):
            setattr(preference, field, min(100, max(0, _integer(request.POST.get(field)) or 0)))
        preference.save()
        return redirect("scoring_settings")
    return render(request, "listings/scoring_settings.html", {
        "preference": preference, "districts": District.objects.all(),
        "repair_types": (("cosmetic", "Косметический"), ("euro", "Евроремонт"),
                         ("withoutRepair", "Без ремонта")),
    })


@login_required
def geo_zones(request):
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "delete":
            GeoZone.objects.filter(pk=request.POST.get("zone_id"), user=request.user).delete()
            return redirect("geo_zones")
        name = (request.POST.get("name") or "").strip()
        tier = request.POST.get("tier")
        try:
            geometry = json.loads(request.POST.get("geometry") or "")
        except (TypeError, ValueError):
            geometry = None
        if (not name or tier not in GeoZone.Tier.values or not isinstance(geometry, dict)
                or geometry.get("type") not in {"Polygon", "MultiPolygon"}):
            messages.error(request, "Укажите название, уровень и GeoJSON Polygon/MultiPolygon.")
            return redirect("geo_zones")
        zone_id = _integer(request.POST.get("zone_id"))
        if zone_id:
            zone = get_object_or_404(GeoZone, pk=zone_id, user=request.user)
            zone.name, zone.tier, zone.geometry, zone.is_active = name, tier, geometry, True
            zone.save(update_fields=("name", "tier", "geometry", "is_active", "updated_at"))
        else:
            GeoZone.objects.create(
                user=request.user, name=name, tier=tier, geometry=geometry, is_active=True,
            )
        messages.success(request, f"Зона «{name}» сохранена.")
        return redirect("geo_zones")
    zones = list(GeoZone.objects.filter(user=request.user))
    return render(request, "listings/geo_zones.html", {
        "zones": zones,
        "zones_json": json.dumps([
            {"id": zone.id, "name": zone.name, "tier": zone.tier, "geometry": zone.geometry}
            for zone in zones
        ], ensure_ascii=False),
        "tiers": GeoZone.Tier.choices,
    })


@login_required
def listing_map(request):
    listings, filters = filtered_listings(request)
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    listings = personal_candidate_queryset(listings, request.user)
    if isinstance(listings, list):
        map_listings = [item for item in listings if item.latitude is not None and item.longitude is not None]
        map_listings.sort(key=lambda item: (item.first_seen_at, item.id), reverse=True)
        map_listings = map_listings[:500]
    else:
        map_listings = list(listings.exclude(latitude__isnull=True).exclude(longitude__isnull=True)
                            .order_by("-first_seen_at")[:500])
    selected_listing_id = _integer(request.GET.get("listing"))
    selected_listing = None
    if selected_listing_id:
        selected_listing = (Listing.objects.filter(pk=selected_listing_id)
                            .filter(area__gte=MIN_TARGET_AREA)
                            .exclude(latitude__isnull=True).exclude(longitude__isnull=True).first())
        if selected_listing and all(item.pk != selected_listing.pk for item in map_listings):
            map_listings.append(selected_listing)
    add_market_position(map_listings)
    return render(request, "listings/map.html", {
        "listings": map_listings, "result_count": len(map_listings), "filters": filters,
        "filter_options": filter_options(), "selected_listing_id": selected_listing.pk if selected_listing else None,
    })


def _geojson_coordinates(coordinates):
    return [[[float(point[1]), float(point[0])] for point in ring] for ring in coordinates]


@login_required
def microdistrict_map(request):
    if request.method == "POST":
        if not request.user.is_staff:
            return HttpResponseForbidden("Только для сотрудников")
        action = request.POST.get("action")
        if action == "link":
            boundary = get_object_or_404(MicrodistrictBoundary, pk=request.POST.get("boundary_id"))
            microdistrict = get_object_or_404(Microdistrict, pk=request.POST.get("microdistrict_id"))
            boundary.microdistrict = microdistrict
            boundary.save(update_fields=["microdistrict", "updated_at"])
            load_polygons.cache_clear()
            messages.success(request, f"Полигон «{boundary.title}» связан с «{microdistrict.name}».")
        elif action == "save_manual":
            try:
                payload = json.loads(request.POST.get("geometry") or "")
                if payload.get("type") != "Polygon":
                    raise ValueError("нужен полигон")
                rings = payload.get("coordinates") or []
                if not rings or len(rings[0]) < 4:
                    raise ValueError("контур должен содержать минимум три точки")
                # Leaflet/GeoJSON stores [longitude, latitude]; the classifier's
                # historical internal format is [latitude, longitude].
                coordinates = []
                for ring in rings:
                    converted = []
                    for point in ring:
                        longitude, latitude = float(point[0]), float(point[1])
                        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                            raise ValueError("некорректные координаты")
                        converted.append([latitude, longitude])
                    coordinates.append(converted)
                geometry = {"type": "Polygon", "coordinates": coordinates}
            except (TypeError, ValueError, json.JSONDecodeError, IndexError):
                messages.error(request, "Не удалось сохранить полигон: проверьте геометрию.")
            else:
                microdistrict = get_object_or_404(Microdistrict, pk=request.POST.get("microdistrict_id"))
                boundary_id = request.POST.get("boundary_id")
                boundary = (MicrodistrictBoundary.objects.filter(pk=boundary_id, source="manual").first()
                            if boundary_id else None)
                points = [point for ring in coordinates for point in ring]
                bbox = [min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)]
                digest = hashlib.sha256(json.dumps(
                    geometry, ensure_ascii=False, sort_keys=True,
                ).encode()).hexdigest()
                if boundary is None:
                    next_id = (MicrodistrictBoundary.objects.filter(source="manual")
                               .aggregate(max_id=Max("source_polygon_id"))["max_id"] or 0) + 1
                    boundary = MicrodistrictBoundary(source="manual", source_polygon_id=next_id)
                boundary.title = microdistrict.name
                boundary.geometry = geometry
                boundary.bbox = bbox
                boundary.geometry_hash = digest
                boundary.microdistrict = microdistrict
                boundary.confidence = Decimal("1.000")
                boundary.priority = 100
                boundary.is_active = True
                boundary.save()
                load_polygons.cache_clear()
                messages.success(request, f"Ручная граница «{microdistrict.name}» сохранена.")
        elif action == "deactivate":
            boundary = get_object_or_404(MicrodistrictBoundary, pk=request.POST.get("boundary_id"), source="manual")
            boundary.is_active = False
            boundary.save(update_fields=["is_active", "updated_at"])
            load_polygons.cache_clear()
            messages.success(request, f"Ручная граница «{boundary.title}» отключена.")
        return redirect("microdistrict_map")

    selected_district = request.GET.get("district", "")
    boundaries = (MicrodistrictBoundary.objects.filter(is_active=True)
                  .select_related("microdistrict__district")
                  .annotate(listing_count=Count(
                      "listings", filter=Q(listings__is_active=True, listings__is_visible=True))))
    if selected_district.isdigit():
        boundaries = boundaries.filter(microdistrict__district_links__district_id=int(selected_district)).distinct()
    polygons = []
    for boundary in boundaries.order_by("title"):
        linked_districts = list(boundary.microdistrict.districts.all()) if boundary.microdistrict_id else []
        polygons.append({
            "id": boundary.pk,
            "title": boundary.title,
            "district": ", ".join(item.name for item in linked_districts) if linked_districts else "Не связан",
            "district_id": linked_districts[0].pk if linked_districts else None,
            "listing_count": boundary.listing_count,
            "linked": bool(boundary.microdistrict_id),
            "microdistrict_id": boundary.microdistrict_id,
            "source": boundary.source,
            "geometry": {"type": "Polygon", "coordinates": _geojson_coordinates(
                (boundary.geometry or {}).get("coordinates", []),
            )},
        })
    return render(request, "listings/microdistrict_map.html", {
        "polygons_json": json.dumps(polygons, ensure_ascii=False),
        "districts": District.objects.all(),
        "selected_district": selected_district,
        "polygon_count": len(polygons),
        "linked_count": sum(item["linked"] for item in polygons),
        "unlinked_boundaries": MicrodistrictBoundary.objects.filter(is_active=True, microdistrict__isnull=True),
        "microdistricts": Microdistrict.objects.select_related("district"),
    })


@login_required
def dashboard(request):
    listings, filters = filtered_listings(request)
    # Headline KPIs follow the visible feed, while local market medians also
    # include hidden listings so every microdistrict keeps a useful baseline.
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

    price_distribution = build_distribution(prices, buckets=7,
        formatter=lambda low, high: f"{round(low / 1_000_000, 1):g}–{round(high / 1_000_000, 1):g} млн")
    area_distribution = build_distribution([float(value) for value in listings.exclude(area__isnull=True).values_list("area", flat=True)], buckets=6,
        formatter=lambda low, high: f"{round(low):g}–{round(high):g}")
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
