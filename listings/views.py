from collections import Counter, defaultdict
import hashlib
import json
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone

from .models import (AvitoDetailPollState, CianDetailPayload, CianDetailPollState, Consideration, District, GlobalListingHide,
                     Listing, ListingReview, ListingSearchQuery,
                     Microdistrict, PriceHistory, Scan, ScoringPreference, SearchQuery,
                     StreetAssignment, UserListingHide, MicrodistrictBoundary, GeoZone)
from .services.enrichment import location_is_visible, parse_address
from .services.location_directory import apply_location_rules_for_assignments, location_is_visible_with_rules
from .services.microdistrict_polygons import load_polygons
from .services.change_history import change_events
from .services.telegram_reviews import handle_update
from .services.personal_filters import annotate_personal_filter
from .services.database_backup import BackupError, create_database_backup
from .services.market import MIN_TARGET_AREA, annotate_market_position
from .services.market import microdistrict_market_stats as _service_microdistrict_market_stats
from .services.scoring import add_listing_score as _service_add_listing_score
from .services.scoring import scoring_preference_for as _service_scoring_preference_for
from .services.reviews import personal_candidate_queryset as _service_personal_candidate_queryset
from .services.listing_queries import (
    filtered_listings as _service_filtered_listings,
    filter_options as _service_filter_options,
    similar_listings as _service_similar_listings,
)
from .views_dashboard import dashboard
from .views_polling_profiles import polling_profiles


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
    """Вернуть следующий двухчасовой слот быстрого опроса Avito."""
    candidates = []
    for day_offset in range(2):
        day = now.date() + timedelta(days=day_offset)
        for hour in range(8, 23, 2):
            candidates.append(now.replace(year=day.year, month=day.month, day=day.day,
                                          hour=hour, minute=15, second=0, microsecond=0))
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


from .views_listings import listing_feed, shortlist
def review_queue(request):
    from .views_reviews import review_queue as _review_queue_view
    return _review_queue_view(request)


def consideration_list(request):
    from .views_reviews import consideration_list as _consideration_list_view
    return _consideration_list_view(request)


def my_reviews(request):
    from .views_reviews import my_reviews as _my_reviews_view
    return _my_reviews_view(request)


from .views_exports import market_export_xlsx, review_export


from .views_listings import hidden_listing_feed, listing_detail




from .views_operations import data_quality, database_backup, disappeared_listings, unavailable_cian_listings
from .views_scan_statistics import scan_statistics
from .views_geo import geo_zones, listing_map, location_directory, microdistrict_map
from .views_preferences import scoring_settings
