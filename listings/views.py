from collections import Counter, defaultdict
import hashlib
import json
from decimal import Decimal, InvalidOperation
from statistics import median

from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render, reverse

from .models import (AvitoDetailPollState, CianDetailPayload, CianDetailPollState, Consideration, District, GlobalListingHide,
                     Listing, ListingReview, ListingSearchQuery,
                     Microdistrict, PriceHistory, ScoringPreference, SearchQuery,
                     StreetAssignment, UserListingHide, MicrodistrictBoundary, GeoZone)
from .services.enrichment import location_is_visible, parse_address
from .services.location_directory import apply_location_rules_for_assignments, location_is_visible_with_rules
from .services.microdistrict_polygons import load_polygons
from .services.change_history import change_events
from .services.telegram_reviews import handle_update
from .services.personal_filters import annotate_personal_filter
from .services.polling_schedule import next_poll_runs, polling_mode_locked
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
# Порог кандидата для просмотра. Меньшие квартиры остаются в базе и выборке
# аналогов, но не показываются как варианты для покупки.


def _integer(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _decimal(value):
    try:
        if value in (None, ""):
            return None
        # Русская раскладка и браузер часто передают десятичную запятую.
        # Decimal принимает только точку, поэтому заменяем её до сохранения
        # введённого пользователем фильтра площади.
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def add_market_position(listings):
    return annotate_market_position(listings)


def scoring_preference_for(user):
    return _service_scoring_preference_for(user)


def add_listing_score(listings, preferences=None):
    """Совместимый импорт view; расчёт оценки находится в сервисном слое."""
    return _service_add_listing_score(listings, preferences)


def personal_candidate_queryset(queryset, user):
    """Совместимый импорт view; фильтрация кандидатов находится в сервисе."""
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
