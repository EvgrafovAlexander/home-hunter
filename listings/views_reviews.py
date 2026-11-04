"""HTTP-обработчики пользовательских оценок и списка «Готов рассмотреть».

Совместимые URL-обёртки остаются в :mod:`listings.views`, а сценарии этого
модуля не смешиваются с общими обработчиками объявлений.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .models import CianDetailPollState, Consideration, Listing, ListingReview
from .services.market import MIN_TARGET_AREA, annotate_market_position
from .services.personal_filters import annotate_personal_filter
from .services.reviews import (
    hide_listing_for_user,
    hide_listing_globally,
    personal_candidate_queryset,
    review_queue_queryset,
    review_form_tag_groups,
    save_review,
)
from .services.scoring import add_listing_score, scoring_preference_for
from .services.market_exclusions import eligible_market_queryset, excluded_listing_ids


def _integer(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@login_required
def review_queue(request):
    queue = personal_candidate_queryset(review_queue_queryset(request.user), request.user)
    if isinstance(queue, list):
        queue = [item for item in queue if item.area is not None and item.area >= MIN_TARGET_AREA]
    requested_id = _integer(request.POST.get("listing_id") if request.method == "POST" else request.GET.get("listing"))
    listing = None
    if requested_id:
        listing = (eligible_market_queryset(Listing.objects.filter(pk=requested_id, is_active=True, is_visible=True))
                   .filter(area__gte=MIN_TARGET_AREA)
                   .exclude(global_hides__is_active=True)
                   .exclude(user_hides__user=request.user).first())
        if listing:
            listing = next(iter(annotate_personal_filter([listing], request.user)), None)
    if request.method == "POST" and listing is None:
        return HttpResponse("Карточка больше недоступна. Обновите очередь; изменения не сохранены.", status=409)
    if listing is None:
        listing = queue[0] if isinstance(queue, list) and queue else queue.first() if not isinstance(queue, list) else None
    if request.method == "POST":
        if not listing:
            return redirect("review_queue")
        action = request.POST.get("action", "review")
        if action == "hide_globally" and not request.user.is_staff:
            raise PermissionDenied
        if action == "hide_personally":
            hide_listing_for_user(listing, request.user)
            return redirect("review_queue")
        if action == "hide_globally":
            reason = request.POST.get("global_reason", "").strip()
            error = "Укажите причину скрытия для всех (до 255 символов)."
            if reason and len(reason) <= 255:
                hide_listing_globally(listing, request.user, reason)
                return redirect("review_queue")
        elif action != "review":
            error = "Неизвестное действие. Обновите страницу."
        elif request.POST.get("hide_personally") or request.POST.get("hide_globally"):
            error = "Скрытие теперь выполняется отдельно от оценки. Обновите страницу."
        else:
            error = save_review(request, listing)
            if not error:
                return redirect("review_queue")
    else:
        error = None
    if listing:
        annotate_market_position([listing])
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
        "item": listing, "tag_groups": review_form_tag_groups(), "error": error,
        "form_data": request.POST if request.method == "POST" else {},
        "selected_tags": request.POST.getlist("tags"),
        "queue_count": review_queue_queryset(request.user).count(),
        "review_photos": review_photos,
        "selected_listing_id": listing.pk if listing else None,
    })


@login_required
def consideration_list(request):
    if request.method == "POST":
        consideration = get_object_or_404(
            Consideration,
            pk=request.POST.get("consideration_id"),
            review__author=request.user,
        )
        stage = request.POST.get("stage")
        if stage in Consideration.Stage.values:
            consideration.stage = stage
            consideration.save(update_fields=("stage", "updated_at"))
        return redirect("consideration_list")
    considerations = (Consideration.objects.filter(review__author=request.user)
                      .exclude(review__listing_id__in=excluded_listing_ids())
                      .select_related("review__listing").order_by("stage", "-updated_at"))
    grouped = [(value, label, [item for item in considerations if item.stage == value])
               for value, label in Consideration.Stage.choices]
    return render(request, "listings/consideration_list.html", {
        "grouped": grouped,
        "stages": Consideration.Stage.choices,
        "total_considerations": considerations.count(),
    })


@login_required
def my_reviews(request):
    reviews = (ListingReview.objects.filter(author=request.user)
               .select_related("listing").prefetch_related("tags"))
    evaluated_count = reviews.count()
    pending_count = review_queue_queryset(request.user).count()
    return render(request, "listings/my_reviews.html", {
        "reviews": reviews,
        "evaluated_count": evaluated_count,
        "pending_count": pending_count,
        "total_count": evaluated_count + pending_count,
        "pending_listings": review_queue_queryset(request.user),
    })
