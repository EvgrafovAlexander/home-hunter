"""Операционные HTTP-обработчики для обслуживания и контроля данных."""

from collections import Counter, defaultdict
from statistics import median

from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse

from .models import (
    CianDetailPollState,
    District,
    Listing,
    ListingSearchQuery,
    Microdistrict,
    PriceHistory,
    SearchQuery,
)
from .services.database_backup import BackupError, create_database_backup
from .services.enrichment import parse_address
from .services.location_directory import location_is_visible_with_rules


@staff_member_required
def database_backup(request):
    """Сформировать резервную копию базы данных по запросу администратора."""
    if request.method == "GET":
        return render(request, "listings/database_backup.html")
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        file_obj, filename, size = create_database_backup()
    except BackupError as exc:
        return render(request, "listings/database_backup.html", {"error": str(exc)}, status=503)
    response = FileResponse(
        file_obj,
        as_attachment=True,
        filename=filename,
        content_type="application/octet-stream",
    )
    response["Content-Length"] = str(size)
    return response


@login_required
def disappeared_listings(request):
    """Показать объявления CIAN, пропущенные полными сканированиями."""
    relations = (
        ListingSearchQuery.objects.filter(
            search_query__source=SearchQuery.Source.CIAN,
            missed_full_scans__gt=0,
        )
        .select_related("listing", "search_query")
        .order_by("-missed_full_scans", "-last_seen_at")
    )
    return render(
        request,
        "listings/disappeared_listings.html",
        {
            "candidates": relations.filter(is_active=True),
            "disappeared": relations.filter(is_active=False),
        },
    )


@login_required
def unavailable_cian_listings(request):
    """Показать объявления CIAN, снятые с публикации на detail-странице."""
    unavailable = list(
        CianDetailPollState.objects.filter(status=CianDetailPollState.Status.UNAVAILABLE)
        .select_related("listing")
        .order_by("-first_unavailable_at", "-last_checked_at")
    )
    listing_ids = [state.listing_id for state in unavailable]

    # Технический статус detail-страницы не является причиной продажи.
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
    status_counts = Counter(state.cian_status_value for state in unavailable)
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
        for row in (
            PriceHistory.objects.filter(listing_id__in=listing_ids, price__isnull=False)
            .order_by("listing_id", "observed_at", "id")
            .values("listing_id", "price")
        ):
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
        "unavailable": unavailable,
        "stats": stats,
    })


@login_required
def data_quality(request):
    """Проверить и вручную исправить неполные геоданные объявлений."""
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
        if microdistrict and (
            not district or microdistrict.district_links.filter(district_id=district.id).exists()
        ):
            district = district or microdistrict.district
        elif microdistrict:
            microdistrict = None
        parsed = parse_address(address, district.name if district else None)
        normalized_microdistrict = microdistrict.name if microdistrict else parsed.microdistrict
        normalized_district = district.name if district else parsed.district
        changed_location = (item.address, item.district, item.microdistrict) != (
            parsed.address,
            normalized_district,
            normalized_microdistrict,
        )
        item.address = item.address_override = parsed.address
        item.district = item.district_override = normalized_district
        item.microdistrict = item.microdistrict_override = normalized_microdistrict
        item.district_ref = district
        item.microdistrict_ref = microdistrict
        item.location_source = "manual"
        item.is_visible = location_is_visible_with_rules(item.address, item.district, item.microdistrict)
        update_fields = [
            "address", "district", "microdistrict", "address_override", "district_override",
            "microdistrict_override", "district_ref", "microdistrict_ref", "location_source", "is_visible",
        ]
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
            label
            for missing_value, label in (
                (not item.address, "Нет адреса"),
                (not item.district_ref, "Нет района"),
                (not item.microdistrict_ref, "Нет микрорайона"),
            )
            if missing_value
        ]
    counts = {name: visible_listings.filter(condition).count() for name, condition in issue_filters.items()}
    counts["all"] = visible_listings.filter(missing).count()
    return render(request, "listings/data_quality.html", {
        "listings": listings,
        "issue": issue,
        "counts": counts,
        "districts": District.objects.all(),
        "microdistricts": Microdistrict.objects.select_related("district").all(),
    })
