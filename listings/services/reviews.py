"""Доменные операции пользовательских оценок и очереди рассмотрения."""

from django.db import transaction

from ..models import (
    Consideration,
    GeoZone,
    GlobalListingHide,
    Listing,
    ListingReview,
    ListingReviewRevision,
    ReviewTag,
    UserListingHide,
)
from .market import MIN_TARGET_AREA
from .personal_filters import annotate_personal_filter, apply_scalar_hard_filters
from .scoring import scoring_preference_for


def personal_candidate_queryset(queryset, user):
    """Применяет жёсткие фильтры и геозоны пользователя к queryset."""
    preference = scoring_preference_for(user)
    queryset = apply_scalar_hard_filters(queryset, preference)
    zones = list(GeoZone.objects.filter(user=user, is_active=True))
    if not zones:
        return queryset
    return annotate_personal_filter(list(queryset[:1000]), user)


def review_queue_queryset(user):
    """Возвращает объявления, ожидающие пользовательской оценки."""
    return Listing.objects.filter(is_active=True, is_visible=True).exclude(
        global_hides__is_active=True,
    ).exclude(
        user_hides__user=user,
    ).filter(
        area__gte=MIN_TARGET_AREA,
    ).exclude(
        reviews__author=user,
    ).order_by("-first_seen_at", "-id")


def review_tag_groups():
    """Группирует активные теги для формы оценки."""
    tags = ReviewTag.objects.filter(is_active=True)
    return [(value, label, list(tags.filter(category=value))) for value, label in ReviewTag.Category.choices]


def save_review(request, listing):
    """Сохраняет оценку, теги и этап рассмотрения из формы пользователя."""
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
            listing=listing,
            author=request.user,
            defaults={
                "rating": rating,
                "decision": decision,
                "comment": request.POST.get("comment", "").strip(),
                "interest_reason": request.POST.get("interest_reason", "").strip(),
                "deal_breaker": request.POST.get("deal_breaker", "").strip(),
            },
        )
        review.tags.set(tags)
        ListingReviewRevision.objects.create(
            review=review,
            rating=review.rating,
            decision=review.decision,
            comment=review.comment,
            interest_reason=review.interest_reason,
            deal_breaker=review.deal_breaker,
            tag_codes=list(review.tags.values_list("code", flat=True)),
        )
        if decision == ListingReview.Decision.CONSIDER:
            Consideration.objects.get_or_create(review=review)
        else:
            Consideration.objects.filter(review=review).delete()
    return None


def hide_listing_for_user(listing, user):
    """Создаёт или возвращает персональное скрытие объявления."""
    return UserListingHide.objects.get_or_create(listing=listing, user=user)


def hide_listing_globally(listing, user, reason):
    """Создаёт или обновляет глобальное скрытие объявления."""
    return GlobalListingHide.objects.update_or_create(
        listing=listing,
        is_active=True,
        defaults={"hidden_by": user, "reason": reason},
    )
