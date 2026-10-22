"""HTTP-обработчики справочников, зон и карт географии объявлений."""

import hashlib
import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Max
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from .models import (
    District,
    GeoZone,
    Listing,
    Microdistrict,
    MicrodistrictBoundary,
    StreetAssignment,
)
from .services.location_directory import apply_location_rules_for_assignments
from .services.market import MIN_TARGET_AREA
from .services.microdistrict_polygons import load_polygons
from .views import (
    _decimal,
    _integer,
    add_market_position,
    filter_options,
    filtered_listings,
    personal_candidate_queryset,
)


@login_required
def location_directory(request):
    """Управлять районами, микрорайонами и правилами улиц."""
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
                    street=assignment.street,
                    house_from=assignment.house_from,
                    house_to=assignment.house_to,
                    parity=assignment.parity,
                )
            district = get_object_or_404(District, pk=request.POST.get("district_id"))
            microdistrict = Microdistrict.objects.filter(pk=request.POST.get("microdistrict_id")).first()
            if microdistrict and not (
                microdistrict.district_id == district.id
                or microdistrict.district_links.filter(district_id=district.id).exists()
            ):
                microdistrict = None
            street = (request.POST.get("street") or "").strip()
            if street:
                values = dict(
                    street=street,
                    house_from=_integer(request.POST.get("house_from")),
                    house_to=_integer(request.POST.get("house_to")),
                    parity=request.POST.get("parity") or StreetAssignment.Parity.ANY,
                    district=district,
                    microdistrict=microdistrict,
                    is_excluded=request.POST.get("is_excluded") == "1",
                    note=(request.POST.get("note") or "").strip(),
                )
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
                apply_location_rules_for_assignments(
                    *(([old_assignment] if action == "street_edit" else [])), assignment,
                )
        elif action == "street_delete":
            assignment = get_object_or_404(StreetAssignment, pk=request.POST.get("assignment_id"))
            old_assignment = StreetAssignment(
                street=assignment.street,
                house_from=assignment.house_from,
                house_to=assignment.house_to,
                parity=assignment.parity,
            )
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
def geo_zones(request):
    """Создавать и удалять пользовательские географические зоны."""
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
        if (
            not name
            or tier not in GeoZone.Tier.values
            or not isinstance(geometry, dict)
            or geometry.get("type") not in {"Polygon", "MultiPolygon"}
        ):
            messages.error(request, "Укажите название, уровень и GeoJSON Polygon/MultiPolygon.")
            return redirect("geo_zones")
        zone_id = _integer(request.POST.get("zone_id"))
        if zone_id:
            zone = get_object_or_404(GeoZone, pk=zone_id, user=request.user)
            zone.name, zone.tier, zone.geometry, zone.is_active = name, tier, geometry, True
            zone.save(update_fields=("name", "tier", "geometry", "is_active", "updated_at"))
        else:
            GeoZone.objects.create(user=request.user, name=name, tier=tier, geometry=geometry, is_active=True)
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
    """Показать подходящие объявления с координатами на карте."""
    listings, filters = filtered_listings(request)
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    listings = personal_candidate_queryset(listings, request.user)
    if isinstance(listings, list):
        map_listings = [item for item in listings if item.latitude is not None and item.longitude is not None]
        map_listings.sort(key=lambda item: (item.first_seen_at, item.id), reverse=True)
        map_listings = map_listings[:500]
    else:
        map_listings = list(
            listings.exclude(latitude__isnull=True)
            .exclude(longitude__isnull=True)
            .order_by("-first_seen_at")[:500]
        )
    selected_listing_id = _integer(request.GET.get("listing"))
    selected_listing = None
    if selected_listing_id:
        selected_listing = (
            Listing.objects.filter(pk=selected_listing_id)
            .filter(area__gte=MIN_TARGET_AREA)
            .exclude(latitude__isnull=True)
            .exclude(longitude__isnull=True)
            .first()
        )
        if selected_listing and all(item.pk != selected_listing.pk for item in map_listings):
            map_listings.append(selected_listing)
    add_market_position(map_listings)
    return render(request, "listings/map.html", {
        "listings": map_listings,
        "result_count": len(map_listings),
        "filters": filters,
        "filter_options": filter_options(),
        "selected_listing_id": selected_listing.pk if selected_listing else None,
    })


def _geojson_coordinates(coordinates):
    """Перевести координаты полигона во внешний формат Leaflet."""
    return [[[float(point[1]), float(point[0])] for point in ring] for ring in coordinates]


@login_required
def microdistrict_map(request):
    """Связывать, рисовать и отключать границы микрорайонов."""
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
                # GeoJSON использует [долгота, широта], а внутренние правила — обратный порядок.
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
                boundary = (
                    MicrodistrictBoundary.objects.filter(pk=boundary_id, source="manual").first()
                    if boundary_id else None
                )
                points = [point for ring in coordinates for point in ring]
                bbox = [min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)]
                digest = hashlib.sha256(json.dumps(
                    geometry, ensure_ascii=False, sort_keys=True,
                ).encode()).hexdigest()
                if boundary is None:
                    next_id = (
                        MicrodistrictBoundary.objects.filter(source="manual")
                        .aggregate(max_id=Max("source_polygon_id"))["max_id"] or 0
                    ) + 1
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
            boundary = get_object_or_404(
                MicrodistrictBoundary,
                pk=request.POST.get("boundary_id"),
                source="manual",
            )
            boundary.is_active = False
            boundary.save(update_fields=["is_active", "updated_at"])
            load_polygons.cache_clear()
            messages.success(request, f"Ручная граница «{boundary.title}» отключена.")
        return redirect("microdistrict_map")

    selected_district = request.GET.get("district", "")
    boundaries = (
        MicrodistrictBoundary.objects.filter(is_active=True)
        .select_related("microdistrict__district")
        .annotate(listing_count=Count(
            "listings",
            filter=Q(listings__is_active=True, listings__is_visible=True),
        ))
    )
    if selected_district.isdigit():
        boundaries = boundaries.filter(
            microdistrict__district_links__district_id=int(selected_district),
        ).distinct()
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
        "unlinked_boundaries": MicrodistrictBoundary.objects.filter(
            is_active=True,
            microdistrict__isnull=True,
        ),
        "microdistricts": Microdistrict.objects.select_related("district"),
    })
