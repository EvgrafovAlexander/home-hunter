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


from .views_listings import hidden_listing_feed, listing_detail




from .views_operations import data_quality, database_backup, disappeared_listings, unavailable_cian_listings
from .views_scan_statistics import scan_statistics
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
