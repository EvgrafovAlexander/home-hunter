"""Личные карточки квартир, посещения, заметки и собственные фотографии."""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ViewingApartment(models.Model):
    class Decision(models.TextChoices):
        THINKING = "thinking", "Думаю"
        SUITABLE = "suitable", "Подходит"
        REJECTED = "rejected", "Отказ"
        ARCHIVED = "archived", "Архив"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="viewing_apartments")
    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание квартиры", blank=True, db_default="")
    cian_url = models.URLField("Ссылка на ЦИАН", max_length=2000, blank=True, db_default="")
    avito_url = models.URLField("Ссылка на Авито", max_length=2000, blank=True, db_default="")
    domclick_url = models.URLField("Ссылка на Домклик", max_length=2000, blank=True, db_default="")
    address = models.CharField("Адрес", max_length=500, blank=True)
    district = models.CharField("Район", max_length=255, blank=True)
    rooms = models.PositiveSmallIntegerField("Комнат", null=True, blank=True)
    area = models.DecimalField("Площадь, м²", max_digits=8, decimal_places=2, null=True, blank=True,
                               validators=[MinValueValidator(1)])
    floor = models.IntegerField("Этаж", null=True, blank=True)
    floors_total = models.PositiveSmallIntegerField("Этажей в доме", null=True, blank=True)
    asking_price = models.PositiveBigIntegerField("Заявленная цена, ₽", null=True, blank=True)
    negotiated_price = models.PositiveBigIntegerField("Обсуждаемая цена, ₽", null=True, blank=True)
    price_note = models.TextField("О торге", blank=True)
    contact_name = models.CharField("Контакт", max_length=200, blank=True)
    contact_phone = models.CharField("Телефон", max_length=60, blank=True)
    contact_role = models.CharField("Кто показывает", max_length=20, blank=True,
                                   choices=(("owner", "Собственник"), ("agent", "Агент"), ("other", "Другое")))
    decision = models.CharField("Решение", max_length=20, choices=Decision.choices, default=Decision.THINKING)
    rating = models.PositiveSmallIntegerField("Моя оценка / 10", null=True, blank=True,
                                             validators=[MinValueValidator(1), MaxValueValidator(10)])
    pros = models.TextField("Плюсы", blank=True)
    cons = models.TextField("Минусы", blank=True)
    questions = models.TextField("Уточнить", blank=True)
    cover_photo = models.ForeignKey("ViewingPhoto", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-pk")
        indexes = [models.Index(fields=("owner", "decision"))]
        constraints = [models.CheckConstraint(condition=models.Q(rating__isnull=True) | models.Q(rating__gte=1, rating__lte=10),
                                              name="viewing_rating_1_to_10")]


class ViewingListingLink(models.Model):
    apartment = models.ForeignKey(ViewingApartment, on_delete=models.CASCADE, related_name="listing_links")
    listing = models.ForeignKey("Listing", on_delete=models.PROTECT, related_name="viewing_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("apartment", "listing"), name="unique_viewing_listing_link")]


class ViewingEntry(models.Model):
    class Kind(models.TextChoices):
        NOTE = "note", "Комментарий"
        VISIT = "visit", "Просмотр"

    class Status(models.TextChoices):
        PLANNED = "planned", "Запланирован"
        COMPLETED = "completed", "Состоялся"
        CANCELLED = "cancelled", "Отменён"

    apartment = models.ForeignKey(ViewingApartment, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField("Тип записи", max_length=10, choices=Kind.choices, default=Kind.NOTE)
    status = models.CharField("Статус просмотра", max_length=10, choices=Status.choices, blank=True)
    happened_at = models.DateTimeField("Дата и время просмотра", null=True, blank=True)
    body = models.TextField("Комментарий", blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")


class ViewingPhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    apartment = models.ForeignKey(ViewingApartment, on_delete=models.CASCADE, related_name="photos")
    entry = models.ForeignKey(ViewingEntry, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField(upload_to="viewings/")
    thumbnail = models.FileField(upload_to="viewings/")
    caption = models.CharField("Подпись", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
