from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil
from statistics import median
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Avg, Count, F, Max, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone

from .models import (CianDetailPollProgress, CianDetailPollState, CianFullScanCheckpoint, Consideration, District, GlobalListingHide,
                     Listing, ListingReview, ListingReviewRevision, ListingSearchQuery, ListingSnapshot, ManualDomclickJob,
                     Microdistrict, PriceHistory, ReviewTag, Scan, ScoringPreference, SearchQuery, SourcePollingControl,
                     StreetAssignment, UserListingHide)
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
        if value in (None, ""):
            return None
        # Browsers and keyboards in the Russian locale commonly submit a
        # decimal comma.  Decimal only accepts a dot, so normalise it before
        # saving a user-entered area filter.
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def filtered_listings(request, *, visible: bool | None = True):
    """Apply the same, deliberately small filter vocabulary to both screens."""
    listings = Listing.objects.exclude(global_hides__is_active=True)
    if request.user.is_authenticated:
        listings = listings.exclude(user_hides__user=request.user)
    if visible is not None:
        listings = listings.filter(is_visible=visible)
    names = ("source", "district", "rooms", "price_min", "price_max", "sqm_price_min",
             "sqm_price_max", "area_min", "area_max", "kitchen_area_min", "kitchen_area_max",
             "floor_min", "floor_max", "repair_type", "building_material_type", "has_lift",
             "has_balcony", "days", "active")
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
    for param, field in (("area_min", "area__gte"), ("area_max", "area__lte"),
                         ("kitchen_area_min", "kitchen_area__gte"),
                         ("kitchen_area_max", "kitchen_area__lte")):
        number = _decimal(values[param])
        if number is not None:
            listings = listings.filter(**{field: number})
    for field in ("repair_type", "building_material_type"):
        if values[field]:
            listings = listings.filter(**{field: values[field]})
    if values["has_lift"] == "1":
        listings = listings.filter(Q(passenger_lifts_count__gt=0) | Q(cargo_lifts_count__gt=0))
    elif values["has_lift"] == "0":
        listings = listings.filter(passenger_lifts_count=0, cargo_lifts_count=0)
    else:
        values["has_lift"] = ""
    if values["has_balcony"] == "1":
        listings = listings.filter(Q(balconies_count__gt=0) | Q(loggias_count__gt=0))
    elif values["has_balcony"] == "0":
        listings = listings.filter(balconies_count=0, loggias_count=0)
    else:
        values["has_balcony"] = ""
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
        "repair_types": (("cosmetic", "Косметический"), ("euro", "Евроремонт"),
                         ("withoutRepair", "Без ремонта")),
        "building_material_types": (("brick", "Кирпичный"), ("monolith", "Монолитный"),
                                    ("panel", "Панельный")),
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


def scoring_preference_for(user):
    return ScoringPreference.objects.filter(user=user).first() or ScoringPreference(user=user)


# Centres of the requested Ufa areas. Their overlapping radii form the target corridors.
UFA_TARGET_POINTS = (
    (54.7248, 55.9473),  # Гостиный двор
    (54.7528, 56.0041),  # проспект Октября / Госцирк
    (54.7418, 55.9776),  # Аграрный университет
    (54.7340, 55.9865),  # Айская
    (54.7200, 55.9960),  # Бакалинская
)


def target_location_score(listing):
    if listing.latitude is None or listing.longitude is None:
        return 50, "локация не уточнена"
    latitude, longitude = float(listing.latitude), float(listing.longitude)
    distance = min((((latitude - point_lat) * 111) ** 2 + ((longitude - point_lon) * 65) ** 2) ** .5
                   for point_lat, point_lon in UFA_TARGET_POINTS)
    if distance <= .7:
        return 100, "в целевой локации"
    if distance <= 1.5:
        return 78, "рядом с целевой локацией"
    if distance <= 2.5:
        return 52, "вне приоритетной зоны"
    return 18, "далеко от целевых локаций"


def condition_score(listing):
    """Prefer explicit CIAN condition; unknown condition remains neutral."""
    explicit_conditions = {
        "euro": (85, "евроремонт указан в карточке"),
        "cosmetic": (60, "косметический ремонт указан в карточке"),
        "withoutRepair": (20, "без ремонта по данным карточки"),
    }
    if listing.repair_type in explicit_conditions:
        return explicit_conditions[listing.repair_type]
    text = f"{listing.title} {listing.description or ''}".lower()
    if any(value in text for value in ("требует ремонта", "под ремонт", "без ремонта", "нужен ремонт")):
        return 20, "требуется ремонт"
    if any(value in text for value in ("дизайнерск", "евроремонт", "качественный ремонт", "новый ремонт", "новым ремонтом")):
        return 85, "есть признаки хорошего ремонта"
    return 50, "состояние не подтверждено"


def add_listing_score(listings, preferences=None):
    """Attach an explainable 0–100 search score to each listing."""
    listings = list(listings)
    preferences = preferences or ScoringPreference()
    history = {}
    for entry in (PriceHistory.objects.filter(listing_id__in=[item.pk for item in listings], price__isnull=False)
                  .order_by("listing_id", "observed_at", "id")):
        history.setdefault(entry.listing_id, [entry.price, entry.price])[1] = entry.price
    now = timezone.now()
    for item in listings:
        reasons = []
        market_delta_pct = getattr(item, "market_delta_pct", None)
        market_samples = getattr(item, "market_samples", 0)
        if market_delta_pct is not None:
            if market_delta_pct <= -10:
                market_score = 75; reasons.append("существенно ниже рынка")
            elif market_delta_pct <= -4:
                market_score = 68; reasons.append("ниже рынка")
            elif market_delta_pct < 0:
                market_score = 68; reasons.append("чуть ниже рынка")
            elif market_delta_pct >= 10:
                market_score = 0; reasons.append("существенно выше рынка")
            elif market_delta_pct >= 4:
                market_score = 22; reasons.append("выше рынка")
            else:
                market_score = 50
        else:
            market_score = 50
        # A discount based on a small sample is informative, but should not dominate the ranking.
        market_confidence = min(1, (market_samples or 0) / 20)
        market_score = 50 + (market_score - 50) * market_confidence
        if market_samples:
            reasons.append(f"цена сопоставлена с {market_samples} аналогами")
        age_days = max(0, (now - item.first_seen_at).total_seconds() / 86400) if item.first_seen_at else 999
        if age_days <= 1:
            freshness_score = 100; reasons.append("добавлено сегодня")
        elif age_days <= 3:
            freshness_score = 80; reasons.append("свежее")
        elif age_days <= 7:
            freshness_score = 65; reasons.append("добавлено за неделю")
        elif age_days <= 30:
            freshness_score = 42
        else:
            freshness_score = 20
        data_score = 0
        for value, points in ((item.address, 35), (item.district, 15), (item.microdistrict, 15),
                              (item.price, 15), (item.area, 10)):
            data_score += points if value not in (None, "") else 0
        if item.image_url:
            data_score += 10; reasons.append("есть фото")
        else:
            reasons.append("нет фото")
        if item.floor and item.floors_total:
            if item.floor == 1 or item.floor == item.floors_total:
                floor_score = 20; reasons.append("крайний этаж")
            else:
                floor_score = 100
        else:
            floor_score = 55
        first_last = history.get(item.pk)
        if first_last and first_last[1] < first_last[0]:
            history_score = 100; reasons.append("цена снижалась")
        elif first_last and first_last[1] > first_last[0]:
            history_score = 25; reasons.append("цена повышалась")
        else:
            history_score = 55
        preference_checks = []
        if preferences.min_area is not None:
            preference_checks.append(item.area is not None and item.area >= preferences.min_area)
            if not preference_checks[-1]: reasons.append("площадь меньше вашей цели")
        if preferences.min_kitchen_area is not None and item.kitchen_area is not None:
            preference_checks.append(item.kitchen_area >= preferences.min_kitchen_area)
            if not preference_checks[-1]: reasons.append("кухня меньше вашей цели")
        if preferences.floor_min is not None:
            preference_checks.append(item.floor is not None and item.floor >= preferences.floor_min)
            if not preference_checks[-1]: reasons.append("этаж ниже вашей цели")
        if preferences.floor_max is not None:
            preference_checks.append(item.floor is not None and item.floor <= preferences.floor_max)
            if not preference_checks[-1]: reasons.append("этаж выше вашей цели")
        if preferences.preferred_districts:
            preference_checks.append(item.district in preferences.preferred_districts)
            if not preference_checks[-1]: reasons.append("район не в ваших предпочтениях")
        if preferences.prefer_photo:
            preference_checks.append(bool(item.image_url))
            if not preference_checks[-1]: reasons.append("нет обязательного фото")
        if preferences.preferred_repair_types and item.repair_type is not None:
            preference_checks.append(item.repair_type in preferences.preferred_repair_types)
            if not preference_checks[-1]: reasons.append("тип ремонта не соответствует предпочтению")
        if preferences.require_lift and (item.passenger_lifts_count is not None or item.cargo_lifts_count is not None):
            preference_checks.append(bool((item.passenger_lifts_count or 0) + (item.cargo_lifts_count or 0)))
            if not preference_checks[-1]: reasons.append("нет лифта")
        if preferences.require_balcony and (item.balconies_count is not None or item.loggias_count is not None):
            preference_checks.append(bool((item.balconies_count or 0) + (item.loggias_count or 0)))
            if not preference_checks[-1]: reasons.append("нет балкона или лоджии")
        if preferences.prefer_furniture and item.has_furniture is not None:
            preference_checks.append(item.has_furniture)
            if not preference_checks[-1]: reasons.append("мебель не указана")
        if preferences.use_ufa_target_zones:
            location_score, location_reason = target_location_score(item)
            reasons.insert(0, location_reason)
        condition_value, condition_reason = condition_score(item)
        reasons.append(condition_reason)
        components = {
            "market_weight": market_score, "freshness_weight": freshness_score,
            "data_weight": data_score, "floor_weight": floor_score,
            "price_history_weight": history_score,
            "condition_weight": condition_value,
        }
        if preference_checks:
            components["preference_weight"] = 100 * sum(preference_checks) / len(preference_checks)
        if preferences.use_ufa_target_zones:
            components["location_weight"] = location_score
        total_weight = sum(getattr(preferences, weight) for weight in components)
        score = sum(value * getattr(preferences, weight) for weight, value in components.items()) / total_weight if total_weight else 0
        item.score = round(max(0, min(100, score)))
        labels = {
            "market_weight": "Цена относительно рынка", "location_weight": "Локация",
            "freshness_weight": "Свежесть", "data_weight": "Полнота данных",
            "floor_weight": "Этаж", "price_history_weight": "История цены",
            "condition_weight": "Состояние квартиры",
            "preference_weight": "Ваши условия",
        }
        item.score_breakdown = [
            {"label": labels[key], "score": round(value), "weight": getattr(preferences, key),
             "contribution": round(value * getattr(preferences, key) / total_weight) if total_weight else 0}
            for key, value in components.items() if getattr(preferences, key)
        ]
        personal_reasons = [
            reason for reason in reasons if reason in {
                "площадь меньше вашей цели", "кухня меньше вашей цели", "этаж ниже вашей цели",
                "этаж выше вашей цели", "район не в ваших предпочтениях", "нет обязательного фото",
                "тип ремонта не соответствует предпочтению", "нет лифта",
                "нет балкона или лоджии", "мебель не указана",
            }
        ]
        item.score_reasons = (personal_reasons + [reason for reason in reasons if reason not in personal_reasons])[:3]
        item.score_label = "Высокий интерес" if item.score >= 75 else (
            "Стоит посмотреть" if item.score >= 60 else "Нейтрально"
        )
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
    add_listing_score(recent, scoring_preference_for(request.user))
    best = [item for item in recent if item.market_state == "below"]
    best.sort(key=lambda item: (item.market_delta_pct, -item.first_seen_at.timestamp()))
    return render(request, "listings/feed.html", {
        "listings": best, "result_count": len(best), "filters": filters,
        "filter_options": filter_options(), "sort": "new", "shortlist": True,
    })


def _review_queue(user):
    return Listing.objects.filter(is_active=True, is_visible=True).exclude(
        global_hides__is_active=True,
    ).exclude(
        user_hides__user=user,
    ).exclude(
        reviews__author=user,
    ).order_by("-first_seen_at", "-id")


def _tag_groups():
    tags = ReviewTag.objects.filter(is_active=True)
    return [(value, label, list(tags.filter(category=value))) for value, label in ReviewTag.Category.choices]


def _save_review(request, listing):
    try:
        rating = int(request.POST.get("rating", ""))
    except ValueError:
        rating = 0
    decision = request.POST.get("decision")
    if not 1 <= rating <= 10 or decision not in ListingReview.Decision.values:
        return "Поставьте оценку от 1 до 10 и выберите решение."
    tags = list(ReviewTag.objects.filter(is_active=True, pk__in=request.POST.getlist("tags")))
    with transaction.atomic():
        review, _ = ListingReview.objects.update_or_create(
            listing=listing, author=request.user,
            defaults={"rating": rating, "decision": decision, "comment": request.POST.get("comment", "").strip(),
                      "interest_reason": request.POST.get("interest_reason", "").strip(),
                      "deal_breaker": request.POST.get("deal_breaker", "").strip()},
        )
        review.tags.set(tags)
        ListingReviewRevision.objects.create(
            review=review, rating=review.rating, decision=review.decision, comment=review.comment,
            interest_reason=review.interest_reason, deal_breaker=review.deal_breaker,
            tag_codes=list(review.tags.values_list("code", flat=True)),
        )
        if decision == ListingReview.Decision.CONSIDER:
            Consideration.objects.get_or_create(review=review)
        else:
            Consideration.objects.filter(review=review).delete()
    return None


@login_required
def review_queue(request):
    listing = _review_queue(request.user).first()
    if request.method == "POST":
        if not listing:
            return redirect("review_queue")
        error = _save_review(request, listing)
        if not error:
            if request.POST.get("hide_personally"):
                UserListingHide.objects.get_or_create(listing=listing, user=request.user)
            if request.user.is_staff and request.POST.get("hide_globally"):
                reason = request.POST.get("global_reason", "").strip() or "Скрыто модератором"
                GlobalListingHide.objects.update_or_create(
                    listing=listing,
                    is_active=True,
                    defaults={"hidden_by": request.user, "reason": reason},
                )
            return redirect("review_queue")
    else:
        error = None
    if listing:
        add_market_position([listing])
        add_listing_score([listing], scoring_preference_for(request.user))
        try:
            review_photos = listing.cian_detail_state.detail_data.get("photos", [])
        except CianDetailPollState.DoesNotExist:
            review_photos = []
        if not review_photos and listing.image_url:
            review_photos = [{"full_url": listing.image_url}]
    else:
        review_photos = []
    return render(request, "listings/review_queue.html", {
        "item": listing, "tag_groups": _tag_groups(), "error": error,
        "queue_count": _review_queue(request.user).count(),
        "review_photos": review_photos,
    })


@login_required
def consideration_list(request):
    if request.method == "POST":
        consideration = get_object_or_404(Consideration, pk=request.POST.get("consideration_id"), review__author=request.user)
        stage = request.POST.get("stage")
        if stage in Consideration.Stage.values:
            consideration.stage = stage
            consideration.save(update_fields=("stage", "updated_at"))
        return redirect("consideration_list")
    considerations = Consideration.objects.filter(review__author=request.user).select_related("review__listing").order_by("stage", "-updated_at")
    grouped = [(value, label, [item for item in considerations if item.stage == value]) for value, label in Consideration.Stage.choices]
    return render(request, "listings/consideration_list.html", {
        "grouped": grouped, "stages": Consideration.Stage.choices,
        "total_considerations": considerations.count(),
    })


@login_required
def my_reviews(request):
    reviews = ListingReview.objects.filter(author=request.user).select_related("listing").prefetch_related("tags")
    evaluated_count = reviews.count()
    pending_count = _review_queue(request.user).count()
    return render(request, "listings/my_reviews.html", {
        "reviews": reviews, "evaluated_count": evaluated_count,
        "pending_count": pending_count, "total_count": evaluated_count + pending_count,
        "pending_listings": _review_queue(request.user),
    })


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
    listing = get_object_or_404(Listing, pk=listing_id)
    own_review = ListingReview.objects.filter(listing=listing, author=request.user).first()
    peer_reviews = (ListingReview.objects.filter(listing=listing).exclude(author=request.user)
                    .select_related("author").prefetch_related("tags")) if own_review else []
    try:
        detail_photos = listing.cian_detail_state.detail_data.get("photos", [])
    except CianDetailPollState.DoesNotExist:
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
        "apartment_attributes": [(label, value) for label, value in apartment_attributes if value is not None],
        "building_attributes": [(label, value) for label, value in building_attributes if value is not None],
        "own_review": own_review, "peer_reviews": peer_reviews,
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
    unavailable = (CianDetailPollState.objects.filter(status=CianDetailPollState.Status.UNAVAILABLE)
                   .select_related("listing").order_by("-first_unavailable_at", "-last_checked_at"))
    return render(request, "listings/unavailable_cian_listings.html", {"unavailable": unavailable})


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
def scoring_settings(request):
    preference, _ = ScoringPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        preference.min_area = _decimal(request.POST.get("min_area"))
        preference.min_kitchen_area = _decimal(request.POST.get("min_kitchen_area"))
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
    district_stats = list(listings.exclude(district__isnull=True).exclude(district="").values("district")
                          .annotate(count=Count("id"), average=Avg("price_per_sqm"), average_price=Avg("price"),
                                    average_area=Avg("area"),
                                    new_count=Count("id", filter=Q(first_seen_at__gte=period_since)))
                          .order_by("-count", "district")[:7])
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
    price_drops = sorted(({
        **change, "percent": round(change["difference"] / change["first"] * 100) if change["first"] else 0,
    } for change in changes if change["difference"] < 0), key=lambda change: change["difference"])[:10]
    price_increases = sorted(({
        **change, "percent": round(change["difference"] / change["first"] * 100) if change["first"] else 0,
    } for change in changes if change["difference"] > 0), key=lambda change: change["difference"], reverse=True)[:10]
    microdistrict_stats = microdistrict_market_stats(listings, period_since, filters)

    def distribution(values, *, buckets, formatter):
        if not values:
            return []
        low, high = min(values), max(values)
        step = max(1, (high - low) / buckets)
        rows = [{"label": formatter(low + step * index, low + step * (index + 1)), "count": 0}
                for index in range(buckets)]
        for value in values:
            rows[min(buckets - 1, int((value - low) / step))]["count"] += 1
        return rows

    price_distribution = distribution(prices, buckets=7,
        formatter=lambda low, high: f"{round(low / 1_000_000, 1):g}–{round(high / 1_000_000, 1):g} млн")
    area_distribution = distribution([float(value) for value in listings.exclude(area__isnull=True).values_list("area", flat=True)], buckets=6,
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
        "microdistrict_min_sample": MICRODISTRICT_MARKET_MIN_SAMPLE,
        "price_drop_count": len(price_drops), "price_unchanged_count": max(0, len(price_changes) - len(changes)),
        "price_increase_count": sum(change["difference"] > 0 for change in changes),
        "price_drops": price_drops, "price_increases": price_increases,
        "price_distribution": price_distribution, "area_distribution": area_distribution,
        "distribution_max": max([1, *(row["count"] for row in price_distribution + area_distribution)]),
        "rooms_two": rooms_two, "rooms_three": rooms_three, "rooms_total": total_rooms,
        "rooms_two_pct": round(rooms_two / total_rooms * 100) if total_rooms else 0,
        "last_updated": listings.aggregate(value=Max("last_seen_at"))["value"],
    })
