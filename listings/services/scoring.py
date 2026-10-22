"""Объяснимый персональный scoring для web-интерфейса и Telegram."""

from listings.models import GeoZone, ScoringPreference


UFA_TARGET_POINTS = (
    (54.7248, 55.9473),
    (54.7528, 56.0041),
    (54.7418, 55.9776),
    (54.7340, 55.9865),
    (54.7200, 55.9960),
)

FIT_WEIGHTS = {
    "location_weight": 40,
    "area_rooms_weight": 25,
    "condition_weight": 15,
    "layout_weight": 10,
    "house_weight": 5,
    "floor_weight": 5,
}


def scoring_preference_for(user):
    return ScoringPreference.objects.filter(user=user).first() or ScoringPreference(user=user)


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


def geo_zone_location_score(listing):
    tier = getattr(listing, "geo_zone_tier", None)
    if tier == GeoZone.Tier.A:
        return 100, "идеальная зона"
    if tier == GeoZone.Tier.B:
        return 80, "хорошая зона"
    if tier == GeoZone.Tier.C:
        return 55, "компромиссная зона"
    if tier == GeoZone.Tier.D:
        return 0, "зона не рассматривается"
    if getattr(listing, "geo_zone_configured", False):
        if listing.latitude is None or listing.longitude is None:
            return 40, "зона не определена: нет координат"
        return 25, "вне заданных зон"
    return target_location_score(listing)


def condition_score(listing):
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


def area_rooms_fit_score(item, preferences):
    if item.area is None:
        area_score = 40
    elif preferences.max_area and item.area > preferences.max_area:
        area_score = 35
    elif preferences.max_area and preferences.min_area:
        midpoint = (float(preferences.min_area) + float(preferences.max_area)) / 2
        distance = abs(float(item.area) - midpoint) / max(midpoint - float(preferences.min_area), 1)
        area_score = round(max(55, 100 - distance * 30))
    else:
        area_score = round(min(100, 70 + max(0, float(item.area) - 55) * 1.5))
    if preferences.preferred_rooms:
        room_score = 100 if item.rooms in {int(value) for value in preferences.preferred_rooms if str(value).isdigit()} else 35
    else:
        room_score = 70 if item.rooms is not None else 45
    return round(area_score * 0.7 + room_score * 0.3)


def layout_fit_score(item):
    scores = []
    if item.kitchen_area is not None and item.area:
        ratio = float(item.kitchen_area) / float(item.area)
        scores.append(100 if ratio >= .20 else 80 if ratio >= .16 else 60 if ratio >= .12 else 35)
    if item.bathrooms_combined is not None or item.bathrooms_separate is not None:
        scores.append(75 if (item.bathrooms_separate or 0) else 60)
    return round(sum(scores) / len(scores)) if scores else 50


def house_fit_score(item):
    signals = [
        80 if item.building_material_type else 50,
        80 if item.built_year else 50,
        80 if item.passenger_lifts_count is not None or item.cargo_lifts_count is not None else 50,
        75 if item.parking_type else 50,
    ]
    return round(sum(signals) / len(signals))


def add_listing_score(listings, preferences=None):
    """Attach an explainable 0–100 Fit Score; market signals remain context."""
    listings = list(listings)
    preferences = preferences or ScoringPreference()
    for item in listings:
        reasons = []
        preference_checks = []
        if preferences.min_area is not None and item.area is not None:
            matches = item.area >= preferences.min_area
            preference_checks.append(matches)
            if not matches:
                reasons.append("площадь меньше вашей цели")
        if preferences.min_kitchen_area is not None:
            if item.kitchen_area is not None:
                matches = item.kitchen_area >= preferences.min_kitchen_area
                preference_checks.append(matches)
            else:
                matches = None
            if matches is False:
                reasons.append("кухня меньше вашей цели")
        if preferences.preferred_repair_types:
            if item.repair_type is not None:
                matches = item.repair_type in preferences.preferred_repair_types
                preference_checks.append(matches)
            else:
                matches = None
            if matches is False:
                reasons.append("тип ремонта не соответствует предпочтению")
        if preferences.prefer_photo:
            matches = bool(item.image_url)
            preference_checks.append(matches)
            if not matches:
                reasons.append("нет обязательного фото")
        if preferences.prefer_furniture:
            if item.has_furniture is not None:
                matches = item.has_furniture is True
                preference_checks.append(matches)
            else:
                matches = None
            if matches is False:
                reasons.append("мебель не указана")
        if preferences.use_ufa_target_zones:
            location_score, location_reason = geo_zone_location_score(item)
            reasons.insert(0, location_reason)
        else:
            location_score = 50
        condition_value, condition_reason = condition_score(item)
        reasons.append(condition_reason)
        market_delta_pct = getattr(item, "market_delta_pct", None)
        if market_delta_pct is not None:
            if market_delta_pct <= -10:
                reasons.append("существенно ниже рынка")
            elif market_delta_pct >= 10:
                reasons.append("существенно выше рынка")
        layout_score = layout_fit_score(item)
        if preference_checks:
            preference_score = 100 * sum(preference_checks) / len(preference_checks)
            layout_score = round(layout_score * .7 + preference_score * .3)
        floor_score = 100 if item.floor and item.floors_total and 1 < item.floor < item.floors_total else 20 if item.floor else 55
        if preferences.floor_min is not None and (item.floor is None or item.floor < preferences.floor_min):
            floor_score = min(floor_score, 30)
            reasons.append("этаж ниже вашей цели")
        if preferences.floor_max is not None and (item.floor is None or item.floor > preferences.floor_max):
            floor_score = min(floor_score, 30)
            reasons.append("этаж выше вашей цели")
        personal_reasons = [reason for reason in reasons if reason in {
            "площадь меньше вашей цели", "кухня меньше вашей цели", "этаж ниже вашей цели", "этаж выше вашей цели", "нет обязательного фото",
            "тип ремонта не соответствует предпочтению", "нет лифта", "нет балкона или лоджии", "мебель не указана",
        }]
        components = {
            "location_weight": location_score,
            "area_rooms_weight": area_rooms_fit_score(item, preferences),
            "condition_weight": condition_value,
            "layout_weight": layout_score,
            "house_weight": house_fit_score(item),
            "floor_weight": floor_score,
        }
        labels = {
            "location_weight": "Зона и локация", "area_rooms_weight": "Площадь и комнаты",
            "condition_weight": "Состояние квартиры", "layout_weight": "Планировка и кухня",
            "house_weight": "Дом", "floor_weight": "Этаж",
        }
        if getattr(item, "geo_zone_configured", False):
            zone_floor = {"A": 76, "B": 51, "C": 26, "D": 0}.get(getattr(item, "geo_zone_tier", None), 0)
            non_location = {key: value for key, value in components.items() if key != "location_weight"}
            non_total = sum(FIT_WEIGHTS[key] for key in non_location)
            non_score = sum(value * FIT_WEIGHTS[key] for key, value in non_location.items()) / non_total if non_total else 0
            item.score = round(zone_floor + non_score * .24)
            item.score_breakdown = [{"label": "Приоритет зоны", "score": round(location_score), "weight": 75, "contribution": zone_floor}]
            item.score_breakdown.extend(
                {"label": labels[key], "score": round(value), "weight": FIT_WEIGHTS[key],
                 "contribution": round(value * FIT_WEIGHTS[key] / non_total * 24) if non_total else 0}
                for key, value in non_location.items()
            )
        else:
            total_weight = sum(FIT_WEIGHTS.values())
            item.score = round(sum(value * FIT_WEIGHTS[key] for key, value in components.items()) / total_weight)
            item.score_breakdown = [
                {"label": labels[key], "score": round(value), "weight": FIT_WEIGHTS[key],
                 "contribution": round(value * FIT_WEIGHTS[key] / total_weight)}
                for key, value in components.items()
            ]
        item.score_reasons = (personal_reasons + [reason for reason in reasons if reason not in personal_reasons])[:3]
        item.score_label = "Высокий интерес" if item.score >= 75 else ("Стоит посмотреть" if item.score >= 60 else "Нейтрально")
    return listings
