"""HTTP-обработчики пользовательской работы с объявлениями."""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import (
    AvitoDetailPollState,
    CianDetailPayload,
    CianDetailPollState,
    Listing,
    ListingReview,
    SearchQuery,
)
from .services.change_history import change_events
from .services.market import MIN_TARGET_AREA
from .services.personal_filters import annotate_personal_filter
from .views import (
    add_listing_score,
    add_market_position,
    filtered_listings,
    filter_options,
    personal_candidate_queryset,
    scoring_preference_for,
    similar_listings,
)


@login_required
def listing_feed(request):
    listings, filters = filtered_listings(request)
    # Малые площади остаются в рыночной статистике, но не показываются кандидатами.
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    preferences = scoring_preference_for(request.user)
    listings = personal_candidate_queryset(listings, request.user)
    ordering = request.GET.get("sort", "new")
    sortings = {
        "new": "-first_seen_at", "price_up": "price", "price_down": "-price",
        "sqm_up": "price_per_sqm", "area_down": "-area", "score": "-id",
    }
    if ordering not in sortings:
        ordering = "new"
    if isinstance(listings, list):
        candidates = listings
        candidate_count = len(candidates)
        candidates.sort(key=lambda item: (item.first_seen_at, item.id), reverse=True)
    else:
        candidate_count = listings.count()
        candidates = list(listings.order_by("-first_seen_at", "-id")[:500])
    if ordering == "score":
        page_listings = candidates[:500]
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
        page_listings.sort(key=lambda item: (-item.score, -item.first_seen_at.timestamp()))
        page_listings = page_listings[:200]
    else:
        page_listings = candidates
        if ordering == "price_up":
            page_listings.sort(key=lambda item: (item.price is None, item.price or 0, -item.id))
        elif ordering == "price_down":
            page_listings.sort(key=lambda item: (item.price is not None, item.price or 0, item.id), reverse=True)
        elif ordering == "sqm_up":
            page_listings.sort(key=lambda item: (item.price_per_sqm is None, item.price_per_sqm or 0, -item.id))
        elif ordering == "area_down":
            page_listings.sort(key=lambda item: (item.area is not None, item.area or 0, item.id), reverse=True)
        page_listings = page_listings[:200]
        add_market_position(page_listings)
        add_listing_score(page_listings, preferences)
    # Показываем только личную оценку текущего пользователя.
    reviews_by_listing = {
        review.listing_id: review
        for review in ListingReview.objects.filter(author=request.user, listing_id__in=[item.id for item in page_listings])
    }
    for item in page_listings:
        item.my_review = reviews_by_listing.get(item.id)
        item.review_state = ("consider" if item.my_review and item.my_review.decision == ListingReview.Decision.CONSIDER
                             else "reject" if item.my_review else "pending")
    return render(request, "listings/feed.html", {
        "listings": page_listings,
        "result_count": candidate_count, "filters": filters, "filter_options": filter_options(),
        "sort": ordering,
    })
@login_required
def shortlist(request):
    listings, filters = filtered_listings(request)
    listings = listings.filter(area__gte=MIN_TARGET_AREA)
    if not request.GET.get("days"):
        listings = listings.filter(first_seen_at__gte=timezone.now() - timedelta(days=7))
        filters["days"] = "7"
    listings = personal_candidate_queryset(listings, request.user)
    recent = (listings if isinstance(listings, list) else list(listings.order_by("-first_seen_at", "-id")[:500]))
    add_market_position(recent)
    add_listing_score(recent, scoring_preference_for(request.user))
    best = [item for item in recent if item.market_state == "below"]
    best.sort(key=lambda item: (item.market_delta_pct, -item.first_seen_at.timestamp()))
    reviews_by_listing = {
        review.listing_id: review
        for review in ListingReview.objects.filter(author=request.user, listing_id__in=[item.id for item in best])
    }
    for item in best:
        item.my_review = reviews_by_listing.get(item.id)
    return render(request, "listings/feed.html", {
        "listings": best, "result_count": len(best), "filters": filters,
        "filter_options": filter_options(), "sort": "new", "shortlist": True,
    })

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
    # Малые площади доступны статистике, но исключены из карточек кандидатов.
    listing = get_object_or_404(Listing, pk=listing_id, area__gte=MIN_TARGET_AREA)
    annotate_personal_filter([listing], request.user)
    own_review = ListingReview.objects.filter(listing=listing, author=request.user).first()
    peer_reviews = (ListingReview.objects.filter(listing=listing).exclude(author=request.user)
                    .select_related("author").prefetch_related("tags")) if own_review else []
    if listing.source == SearchQuery.Source.AVITO:
        try:
            detail_photos = listing.avito_detail_state.detail_data.get("photos", [])
            detail_state = listing.avito_detail_state
        except AvitoDetailPollState.DoesNotExist:
            detail_photos, detail_state = [], None
    else:
        try:
            detail_photos = listing.cian_detail_state.detail_data.get("photos", [])
            detail_state = listing.cian_detail_state
        except CianDetailPollState.DoesNotExist:
            detail_photos, detail_state = [], None
    # Для снятого объявления берём фотографии из последнего успешного payload.
    if not detail_photos:
        if listing.source == SearchQuery.Source.CIAN:
            try:
                archived_photos = (listing.cian_detail_payload.payload.get("offer", {}).get("photos", []))
            except CianDetailPayload.DoesNotExist:
                archived_photos = []
            detail_photos = [{"full_url": photo.get("fullUrl") or photo.get("full_url")} for photo in archived_photos if photo.get("fullUrl") or photo.get("full_url")]
        elif listing.source == SearchQuery.Source.AVITO:
            try:
                detail_photos = listing.avito_detail_payload.payload.get("photos", [])
            except AvitoDetailPayload.DoesNotExist:
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
        "detail_state": detail_state,
        "apartment_attributes": [(label, value) for label, value in apartment_attributes if value is not None],
        "building_attributes": [(label, value) for label, value in building_attributes if value is not None],
        "own_review": own_review, "peer_reviews": peer_reviews,
    })
