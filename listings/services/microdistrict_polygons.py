"""Classify coordinates using the cached Ufa microdistrict polygons."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from listings.models import Microdistrict, MicrodistrictBoundary


POLYGONS_PATH = Path(settings.BASE_DIR) / "data" / "ufa_microdistrict_polygons.json"
BOUNDARY_EPSILON = 1e-10


@dataclass(frozen=True)
class PolygonMatch:
    polygon_id: int
    title: str
    confidence: float


def _point_on_segment(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> bool:
    cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
    if abs(cross) > BOUNDARY_EPSILON:
        return False
    return min(ax, bx) - BOUNDARY_EPSILON <= x <= max(ax, bx) + BOUNDARY_EPSILON and \
        min(ay, by) - BOUNDARY_EPSILON <= y <= max(ay, by) + BOUNDARY_EPSILON


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> tuple[bool, bool]:
    inside = False
    for index, (lat_a, lon_a) in enumerate(ring):
        lat_b, lon_b = ring[index - 1]
        ax, ay, bx, by = lon_a, lat_a, lon_b, lat_b
        if _point_on_segment(x, y, ax, ay, bx, by):
            return True, True
        if (ay > y) != (by > y):
            intersection_x = (bx - ax) * (y - ay) / (by - ay) + ax
            if x < intersection_x:
                inside = not inside
    return inside, False


def _point_in_polygon(latitude: float, longitude: float, rings: list[list[list[float]]]) -> tuple[bool, bool]:
    if not rings:
        return False, False
    exterior, boundary = _point_in_ring(longitude, latitude, rings[0])
    if not exterior:
        return False, boundary
    for hole in rings[1:]:
        in_hole, on_boundary = _point_in_ring(longitude, latitude, hole)
        if on_boundary:
            return True, True
        if in_hole:
            return False, False
    return True, boundary


@lru_cache(maxsize=1)
def load_polygons() -> tuple[dict, ...]:
    # The database is the authoritative store once boundaries are imported.
    # Keep the JSON file as a safe bootstrap/fallback for first deployment.
    try:
        rows = list(MicrodistrictBoundary.objects.filter(is_active=True).only(
            "source_polygon_id", "title", "geometry", "confidence",
        ))
    except (OperationalError, ProgrammingError, RuntimeError):
        rows = []
    if rows:
        return tuple({
            "id": row.source_polygon_id,
            "title": row.title,
            "geometry_type": (row.geometry or {}).get("type", "Polygon"),
            "coordinates": (row.geometry or {}).get("coordinates", []),
            "confidence": float(row.confidence),
        } for row in rows)
    if not POLYGONS_PATH.exists():
        return ()
    payload = json.loads(POLYGONS_PATH.read_text(encoding="utf-8"))
    return tuple(payload.get("items", ()))


def resolve_microdistrict(latitude, longitude) -> PolygonMatch | None:
    """Return the containing microdistrict, or None for unknown coordinates."""
    if latitude is None or longitude is None:
        return None
    latitude, longitude = float(latitude), float(longitude)
    matches = []
    for polygon in load_polygons():
        rings = polygon.get("coordinates") or []
        if not rings:
            continue
        all_points = [point for ring in rings for point in ring]
        latitudes = [point[0] for point in all_points]
        longitudes = [point[1] for point in all_points]
        if not (min(latitudes) - BOUNDARY_EPSILON <= latitude <= max(latitudes) + BOUNDARY_EPSILON and
                min(longitudes) - BOUNDARY_EPSILON <= longitude <= max(longitudes) + BOUNDARY_EPSILON):
            continue
        inside, boundary = _point_in_polygon(latitude, longitude, rings)
        if inside:
            matches.append((boundary, polygon))
    if not matches:
        return None
    # Boundary matches are deliberately lower confidence than points well inside.
    boundary, polygon = sorted(matches, key=lambda item: (not item[0], item[1]["id"]))[0]
    return PolygonMatch(polygon["id"], polygon["title"], 0.85 if boundary else 0.98)


def canonical_microdistrict_name(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value.replace("ё", "е").strip().casefold())
    value = re.sub(r"^(?:м-н|мкр\.?|микрорайон)\s+", "", value)
    value = re.sub(r"\s+(?:м-н|мкр\.?|микрорайон|ж/к|жк|пос\.?|поселок)$", "", value)
    return value.strip()


def normalized_microdistrict_label(value: str | None) -> str:
    """Return a readable label without source-specific suffixes."""
    value = unicodedata.normalize("NFKC", value or "").replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^(?:м-н|мкр\.?|микрорайон)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:м-н|мкр\.?|микрорайон|ж/к|жк|пос\.?|поселок)$", "", value, flags=re.I)
    return value.strip(" ,.-")


def find_microdistrict_ref(title: str, microdistricts):
    target = canonical_microdistrict_name(title)
    for item in microdistricts:
        names = [item.name, *(item.aliases or [])]
        if target in {canonical_microdistrict_name(name) for name in names}:
            return item
    return None


def enrich_listing_from_coordinates(listing) -> set[str]:
    """Fill a listing's missing location fields as soon as coordinates exist."""
    if (listing.location_source == "manual" or listing.microdistrict_override or
            listing.microdistrict or listing.latitude is None or listing.longitude is None):
        return set()
    match = resolve_microdistrict(listing.latitude, listing.longitude)
    if not match:
        return set()
    directory = Microdistrict.objects.select_related("district").all()
    microdistrict_ref = find_microdistrict_ref(match.title, directory)
    canonical_name = microdistrict_ref.name if microdistrict_ref else normalized_microdistrict_label(match.title)
    boundary = MicrodistrictBoundary.objects.filter(
        source="bezposrednikov", source_polygon_id=match.polygon_id, is_active=True,
    ).first()
    listing.microdistrict = canonical_name
    listing.microdistrict_source = "polygon"
    listing.microdistrict_confidence = match.confidence
    listing.microdistrict_polygon_id = match.polygon_id
    listing.microdistrict_boundary = boundary
    changed = {
        "microdistrict", "microdistrict_source", "microdistrict_confidence",
        "microdistrict_polygon_id", "microdistrict_boundary",
    }
    if microdistrict_ref and not listing.district and not listing.district_ref:
        listing.district = microdistrict_ref.district.name
        listing.district_ref = microdistrict_ref.district
        listing.microdistrict_ref = microdistrict_ref
        changed.update({"district", "district_ref", "microdistrict_ref"})
    elif microdistrict_ref:
        listing.microdistrict_ref = microdistrict_ref
        changed.add("microdistrict_ref")
    return changed
