"""Личные карточки и записи просмотров: выборки, транзакции и связи с объявлениями.

Сетевых запросов нет. Обработка изображений и согласование с backup вынесены
в viewing_photos; пользовательские сведения не меняют данные сборщиков.
"""

from urllib.parse import urlsplit
import re
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from listings.models import Listing, ViewingApartment, ViewingEntry, ViewingListingLink, ViewingPhoto
from .viewing_photos import viewing_media_lock

APARTMENT_FIELDS = ("title", "address", "district", "rooms", "area", "floor", "floors_total", "asking_price",
                    "negotiated_price", "price_note", "contact_name", "contact_phone", "contact_role", "decision",
                    "rating", "pros", "cons", "questions")
MAX_PHOTOS = 100


def apartment_queryset(owner, *, query="", decision=""):
    """Выбирает только личные квартиры, добавляя ближайший просмотр и счётчики."""
    upcoming = ViewingEntry.objects.filter(apartment_id=OuterRef("pk"), kind="visit", status="planned",
                                           happened_at__gte=timezone.now()).order_by("happened_at")
    apartments = (ViewingApartment.objects.filter(owner=owner).select_related("cover_photo")
                  .annotate(next_visit=Subquery(upcoming.values("happened_at")[:1]),
                            photo_count=Count("photos", distinct=True), entry_count=Count("entries", distinct=True)))
    if decision in ViewingApartment.Decision.values:
        apartments = apartments.filter(decision=decision)
    elif decision != "all":
        apartments = apartments.exclude(decision="archived")
    if query:
        apartments = apartments.filter(Q(title__icontains=query) | Q(address__icontains=query) | Q(district__icontains=query))
    return apartments.order_by("-updated_at", "-pk")


def resolve_viewing_listing(reference, *, allowed_hosts):
    """Находит объявление по внутреннему ID или ссылке HomeHunter, без загрузки URL."""
    value = str(reference).strip()
    if not value.isascii():
        raise ValidationError("Укажите числовой ID или ссылку на объявление HomeHunter.")
    if not value.isdigit():
        try:
            parsed = urlsplit(value)
            if parsed.netloc and (parsed.scheme not in {"http", "https"} or parsed.hostname not in allowed_hosts):
                raise ValueError
            if parsed.scheme and not parsed.netloc:
                raise ValueError
            match = re.fullmatch(r"/listings/([0-9]+)/?", parsed.path)
            if not match:
                raise ValueError
            value = match[1]
        except ValueError as exc:
            raise ValidationError("Нужен ID HomeHunter или ссылка вида /listings/123/.") from exc
    if not value or len(value) > 19 or not 0 < int(value) < 2**63:
        raise ValidationError("Некорректный ID объявления.")
    listing = Listing.objects.filter(pk=int(value)).first()
    if not listing:
        raise ValidationError("Объявление с таким ID не найдено в HomeHunter.")
    return listing


def apartment_initial(listing):
    """Подготавливает однократное заполнение карточки без последующей синхронизации."""
    return {"title": (listing.address or listing.title)[:200], "address": listing.address or "",
            "district": listing.district or "", "rooms": listing.rooms, "area": listing.area,
            "floor": listing.floor, "floors_total": listing.floors_total, "asking_price": listing.price}


def linked_apartments(owner, listing):
    """Возвращает личные карточки, куда уже добавлено объявление, включая архив."""
    return ViewingApartment.objects.filter(owner=owner, listing_links__listing=listing)


@transaction.atomic
def save_apartment(owner, cleaned, *, apartment_id=None, listing=None):
    """Создаёт или обновляет карточку с проверкой владельца и версии формы."""
    if apartment_id:
        apartment = ViewingApartment.objects.select_for_update().get(pk=apartment_id, owner=owner)
        if cleaned.get("version") != apartment.version:
            raise ValidationError("Карточка уже изменена в другой вкладке. Обновите страницу перед сохранением.")
        apartment.version += 1
    else:
        apartment = ViewingApartment(owner=owner)
    for field in APARTMENT_FIELDS:
        setattr(apartment, field, cleaned[field])
    apartment.full_clean(exclude=["cover_photo"])
    apartment.save()
    if listing:
        ViewingListingLink.objects.get_or_create(apartment=apartment, listing=listing)
    return apartment


@transaction.atomic
def change_listing_link(owner, apartment_id, listing, *, remove=False):
    """Идемпотентно добавляет или убирает связь, не изменяя сведения квартиры."""
    apartment = ViewingApartment.objects.select_for_update().get(pk=apartment_id, owner=owner)
    links = apartment.listing_links
    if remove:
        links.filter(listing=listing).delete()
    else:
        if links.count() >= 20 and not links.filter(listing=listing).exists():
            raise ValidationError("В карточку можно добавить до 20 объявлений.")
        ViewingListingLink.objects.get_or_create(apartment=apartment, listing=listing)


def save_viewing_entry(owner, apartment_id, cleaned, prepared_photos, *, entry_id=None):
    """Сохраняет запись и фото атомарно; при ошибке удаляет только новые файлы."""
    written = []
    with viewing_media_lock():
        try:
            with transaction.atomic():
                apartment = ViewingApartment.objects.select_for_update().get(pk=apartment_id, owner=owner)
                if apartment.photos.count() + len(prepared_photos) > MAX_PHOTOS:
                    raise ValidationError("В одной карточке можно хранить до 100 фотографий.")
                if entry_id:
                    entry = ViewingEntry.objects.select_for_update().get(pk=entry_id, apartment=apartment)
                    if cleaned.get("version") != entry.version:
                        raise ValidationError("Запись уже изменена в другой вкладке. Обновите страницу.")
                    entry.version += 1
                else:
                    entry = ViewingEntry(apartment=apartment)
                for field in ("kind", "status", "happened_at", "body"):
                    setattr(entry, field, cleaned[field])
                entry.full_clean()
                entry.save()
                for full_bytes, thumb_bytes in prepared_photos:
                    photo = ViewingPhoto(apartment=apartment, entry=entry)
                    for field, content, suffix in ((photo.image, full_bytes, ""), (photo.thumbnail, thumb_bytes, "-thumb")):
                        field.save(f"{uuid.uuid4().hex}{suffix}.jpg", ContentFile(content), save=False)
                        written.append((field.storage, field.name))
                    photo.save()
                    if apartment.cover_photo_id is None:
                        apartment.cover_photo = photo
                apartment.save(update_fields=("cover_photo", "updated_at"))
            return entry
        except Exception:
            for storage, name in written:
                storage.delete(name)
            raise


def update_viewing_photo(owner, photo_id, *, action, caption=""):
    """Изменяет личное фото; физическое удаление выполняется только после commit."""
    with viewing_media_lock(), transaction.atomic():
        photo = ViewingPhoto.objects.select_related("apartment").get(pk=photo_id, apartment__owner=owner)
        apartment = ViewingApartment.objects.select_for_update().get(pk=photo.apartment_id, owner=owner)
        if action == "cover":
            apartment.cover_photo = photo
            apartment.save(update_fields=("cover_photo", "updated_at"))
        elif action == "caption":
            photo.caption = caption
            photo.full_clean()
            photo.save(update_fields=("caption",))
        elif action == "delete":
            files = [(field.storage, field.name) for field in (photo.image, photo.thumbnail)]
            photo.delete()
            if apartment.cover_photo_id == photo_id:
                apartment.cover_photo = apartment.photos.first()
                apartment.save(update_fields=("cover_photo", "updated_at"))
            def remove_files():
                with viewing_media_lock():
                    for storage, name in files:
                        storage.delete(name)
            transaction.on_commit(remove_files, robust=True)
        else:
            raise ValidationError("Неизвестное действие с фотографией.")
