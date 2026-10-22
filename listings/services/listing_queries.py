"""Запросы и фильтры для экранов списка объявлений."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from listings.models import Listing, SearchQuery


def _integer(value):
    """Безопасно преобразует параметр формы в целое число."""
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _decimal(value):
    """Безопасно преобразует параметр площади в Decimal."""
    try:
        if value in (None, ""):
            return None
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def filtered_listings(request, *, visible=True):
    """Применяет единый набор фильтров к списку объявлений."""
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


def filter_options(*, visible=True):
    """Возвращает значения фильтров, доступные в текущем наборе данных."""
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


def similar_listings(listing, limit=6):
    """Находит локальные альтернативы с близкой площадью."""
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
