"""Модели пользовательских оценок, consideration и скрытия объявлений."""

from django.conf import settings
from django.db import models

from .models import Listing


class ReviewTag(models.Model):
    """Справочник тегов, которыми пользователь поясняет оценку."""

    class Category(models.TextChoices):
        CONDITION = "condition", "Состояние"
        LAYOUT = "layout", "Планировка"
        BUILDING = "building", "Дом и окружение"
        MONEY = "money", "Деньги"
        RISK = "risk", "Риски"
        PLUS = "plus", "Плюсы"

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=20, choices=Category.choices)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("category", "sort_order", "name")

    def __str__(self):
        return self.name


class ListingReview(models.Model):
    """Пользовательская оценка объявления и решение по нему."""

    class Decision(models.TextChoices):
        REJECT = "reject", "Не рассматривать"
        CONSIDER = "consider", "Готов рассмотреть"

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="reviews")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listing_reviews")
    rating = models.PositiveSmallIntegerField()
    decision = models.CharField(max_length=12, choices=Decision.choices)
    comment = models.TextField(blank=True)
    interest_reason = models.TextField(blank=True)
    deal_breaker = models.TextField(blank=True)
    tags = models.ManyToManyField(ReviewTag, blank=True, related_name="reviews")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("listing", "author"), name="unique_listing_review_author"),
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=10), name="review_rating_1_to_10"),
        ]
        ordering = ("-updated_at",)


class ListingReviewRevision(models.Model):
    """Историческая версия пользовательской оценки."""

    review = models.ForeignKey(ListingReview, on_delete=models.CASCADE, related_name="revisions")
    rating = models.PositiveSmallIntegerField()
    decision = models.CharField(max_length=12, choices=ListingReview.Decision.choices)
    comment = models.TextField(blank=True)
    interest_reason = models.TextField(blank=True)
    deal_breaker = models.TextField(blank=True)
    tag_codes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)


class TelegramReviewSession(models.Model):
    """Черновик пошаговой оценки объявления в Telegram."""

    class State(models.TextChoices):
        RATING = "rating", "Оценка"
        TAGS = "tags", "Теги"
        INTEREST = "interest", "Причина интереса"
        DEAL_BREAKER = "deal_breaker", "Стоп-фактор"
        COMMENT = "comment", "Комментарий"
        DECISION = "decision", "Решение"

    telegram_user_id = models.CharField(max_length=64)
    telegram_chat_id = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telegram_review_sessions")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="telegram_review_sessions")
    state = models.CharField(max_length=20, choices=State.choices, default=State.RATING)
    category_index = models.PositiveSmallIntegerField(default=0)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    decision = models.CharField(max_length=12, blank=True)
    tag_codes = models.JSONField(default=list, blank=True)
    interest_reason = models.TextField(blank=True)
    deal_breaker = models.TextField(blank=True)
    comment = models.TextField(blank=True)
    channel_message_id = models.BigIntegerField(null=True, blank=True)
    channel_chat_id = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("telegram_user_id", "listing"), name="unique_telegram_review_session")]


class Consideration(models.Model):
    """Этап shortlist для оценки с решением рассмотреть объявление."""

    class Stage(models.TextChoices):
        NEW = "new", "Новое"
        PLANNED = "planned", "Просмотр запланирован"
        VIEWED = "viewed", "Просмотрено"
        ARCHIVED = "archived", "Архив"

    review = models.OneToOneField(ListingReview, on_delete=models.CASCADE, related_name="consideration")
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.NEW)
    updated_at = models.DateTimeField(auto_now=True)


class UserListingHide(models.Model):
    """Персональное скрытие объявления пользователем."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="user_hides")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listing_hides")
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("listing", "user"), name="unique_user_listing_hide")]


class GlobalListingHide(models.Model):
    """Глобальное модераторское скрытие объявления."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="global_hides")
    hidden_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="global_listing_hides")
    reason = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("listing",), condition=models.Q(is_active=True), name="unique_active_global_listing_hide",
        )]
