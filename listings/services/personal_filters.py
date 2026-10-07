"""User hard filters and lightweight GeoJSON zone checks.

The first implementation intentionally avoids a PostGIS dependency.  Zone
geometries are stored as GeoJSON and evaluated in a background-friendly,
deterministic Python service; the model can be moved to PostGIS later without
changing the user-facing contract.
"""

from listings.models import GeoZone, Listing, ScoringPreference


def _point_on_segment(x, y, x1, y1, x2, y2):
    cross = (y - y1) * (x2 - x1) - (x - x1) * (y2 - y1)
    if abs(cross) > 1e-9:
        return False
    return min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for index, current in enumerate(ring):
        x1, y1 = current
        x2, y2 = ring[(index + 1) % len(ring)]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return inside


def _point_in_polygon(point, coordinates):
    if not coordinates or not _point_in_ring(point, coordinates[0]):
        return False
    return not any(_point_in_ring(point, hole) for hole in coordinates[1:])


def geometry_contains(geometry, latitude, longitude):
    """Return whether a lat/lon point is inside a GeoJSON Polygon/MultiPolygon."""
    if not geometry or latitude is None or longitude is None:
        return False
    try:
        point = (float(longitude), float(latitude))
        if geometry.get("type") == "Polygon":
            polygons = [geometry.get("coordinates", [])]
        elif geometry.get("type") == "MultiPolygon":
            polygons = geometry.get("coordinates", [])
        else:
            return False
        return any(_point_in_polygon(point, polygon) for polygon in polygons)
    except (AttributeError, TypeError, ValueError, IndexError):
        return False


def zone_for_listing(listing: Listing, zones=None):
    """Return the highest-priority matching tier for a listing, or ``None``.

    D is deliberately checked first so an accidental overlapping D polygon
    always excludes the listing.
    """
    if listing.latitude is None or listing.longitude is None:
        return None
    user_id = getattr(listing, "_personal_filter_user_id", None)
    zones = list(zones if zones is not None else GeoZone.objects.filter(user_id=user_id, is_active=True))
    matched = [zone for zone in zones if geometry_contains(zone.geometry, listing.latitude, listing.longitude)]
    if not matched:
        return None
    order = {GeoZone.Tier.D: 0, GeoZone.Tier.A: 1, GeoZone.Tier.B: 2, GeoZone.Tier.C: 3}
    return sorted(matched, key=lambda zone: (order.get(zone.tier, 9), zone.id))[0].tier


def hard_filter_result(listing: Listing, preference: ScoringPreference, *, zones=None):
    """Return ``(allowed, reason, zone_tier)`` for the configured hard rules."""
    if preference.max_price is not None and (listing.price is None or listing.price > preference.max_price):
        return False, "цена выше абсолютного максимума", None
    if preference.min_area is not None and (listing.area is None or listing.area < preference.min_area):
        return False, "площадь меньше минимальной", None
    if preference.max_area is not None and (listing.area is None or listing.area > preference.max_area):
        return False, "площадь больше максимальной", None
    if preference.preferred_rooms:
        allowed_rooms = {int(value) for value in preference.preferred_rooms if str(value).isdigit()}
        if listing.rooms is None or listing.rooms not in allowed_rooms:
            return False, "комнатность не входит в выбранные", None
    if preference.preferred_districts and (not listing.district or listing.district not in preference.preferred_districts):
        return False, "район не входит в выбранные", None
    if preference.require_lift and (listing.passenger_lifts_count is None and listing.cargo_lifts_count is None):
        # Unknown is not a hard failure: source cards commonly omit building details.
        pass
    elif preference.require_lift and not ((listing.passenger_lifts_count or 0) + (listing.cargo_lifts_count or 0)):
        return False, "нет лифта", None
    if preference.require_balcony and (listing.balconies_count is not None or listing.loggias_count is not None):
        if not ((listing.balconies_count or 0) + (listing.loggias_count or 0)):
            return False, "нет балкона или лоджии", None

    zone_tier = None
    if zones is not None or getattr(listing, "_personal_filter_user_id", None):
        zone_tier = zone_for_listing(listing, zones)
        if zone_tier == GeoZone.Tier.D:
            return False, "квартира попадает в зону D", zone_tier
    return True, "", zone_tier


def annotate_personal_filter(listings, user):
    """Annotate an iterable without changing the queryset or persisted data."""
    preference = ScoringPreference.objects.filter(user=user).first() or ScoringPreference(user=user)
    zones = list(GeoZone.objects.filter(user=user, is_active=True))
    result = []
    for listing in listings:
        listing._personal_filter_user_id = user.id
        allowed, reason, tier = hard_filter_result(listing, preference, zones=zones)
        listing.hard_filter_allowed = allowed
        listing.hard_filter_reason = reason
        listing.geo_zone_tier = tier
        listing.geo_zone_configured = bool(zones)
        if allowed:
            result.append(listing)
    return result


def apply_scalar_hard_filters(queryset, preference):
    """Push filters that do not require geometry into SQL."""
    if preference.max_price is not None:
        queryset = queryset.filter(price__isnull=False, price__lte=preference.max_price)
    if preference.min_area is not None:
        queryset = queryset.filter(area__isnull=False, area__gte=preference.min_area)
    if preference.max_area is not None:
        queryset = queryset.filter(area__isnull=False, area__lte=preference.max_area)
    if preference.preferred_rooms:
        rooms = [int(value) for value in preference.preferred_rooms if str(value).isdigit()]
        if rooms:
            queryset = queryset.filter(rooms__in=rooms)
    if preference.preferred_districts:
        queryset = queryset.filter(district__in=preference.preferred_districts)
    if preference.require_lift:
        queryset = queryset.exclude(passenger_lifts_count=0, cargo_lifts_count=0)
    if preference.require_balcony:
        queryset = queryset.exclude(balconies_count=0, loggias_count=0)
    return queryset
