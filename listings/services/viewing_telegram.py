"""Очередь и отправка карточек просмотров в Telegram.

Сервис не меняет личную карточку и не импортирует обсуждения обратно в Home
Hunter. Внешние вызовы выполняются только worker после постановки в очередь.
"""

import html
import json
import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from listings.models import ListingReview, ViewingApartment, ViewingTelegramPublication

logger = logging.getLogger(__name__)


def queue_viewing_publication(owner, apartment_id):
    """Ставит карточку владельца в очередь публикации без сетевого вызова."""
    if not settings.TG_VIEWINGS_CHANNEL_ID:
        raise ValueError("TG_VIEWINGS_CHANNEL_ID не настроен")
    with transaction.atomic():
        apartment = ViewingApartment.objects.select_for_update().get(pk=apartment_id, owner=owner)
        publication, _ = ViewingTelegramPublication.objects.update_or_create(
            apartment=apartment,
            defaults={"channel_id": settings.TG_VIEWINGS_CHANNEL_ID, "status": "pending", "error": "", "published_at": None},
        )
    return publication


def _api(method, *, data=None, files=None):
    """Выполняет вызов Bot API и возвращает result, не логируя секреты."""
    if not settings.TG_BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN не настроен")
    proxies = {"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL} if settings.TELEGRAM_PROXY_URL else None
    response = requests.post(
        f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/{method}",
        data=data, files=files, proxies=proxies, timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram API error"))
    return payload.get("result")


def _money(value):
    return f"{value:,}".replace(",", " ") if value is not None else "—"


def _post_text(apartment):
    """Формирует короткую подпись основного поста из публичных полей."""
    parts = [f"🏠 <b>{html.escape(apartment.title)}</b>"]
    location = " · ".join(filter(None, (apartment.address, apartment.district)))
    if location:
        parts.append(html.escape(location))
    facts = " · ".join(filter(None, (
        f"{apartment.rooms} комн." if apartment.rooms is not None else None,
        f"{apartment.area} м²" if apartment.area is not None else None,
        f"этаж {apartment.floor}/{apartment.floors_total}" if apartment.floor is not None else None,
        f"{_money(apartment.asking_price)} ₽" if apartment.asking_price is not None else None,
    )))
    if facts:
        parts.append(html.escape(facts))
    parts.append(f"<b>Статус:</b> {html.escape(apartment.get_decision_display())}")
    if apartment.rating:
        parts.append(f"<b>Моя оценка:</b> {apartment.rating}/10")
    for label, value in (("Плюсы", apartment.pros), ("Минусы", apartment.cons), ("Уточнить", apartment.questions)):
        if value:
            parts.append(f"<b>{label}:</b> {html.escape(value[:350])}")
    review = (ListingReview.objects.filter(listing__viewing_links__apartment=apartment, author=apartment.owner)
              .order_by("-updated_at").first())
    if review:
        parts.append(f"<b>Рецензия Home Hunter:</b> {review.rating}/10 · {html.escape(review.get_decision_display())}")
    parts.append(f"<a href=\"{settings.PUBLIC_BASE_URL}/viewings/{apartment.pk}/\">Открыть карточку</a>")
    return "\n\n".join(parts)[:1024]


def _description_text(apartment):
    """Формирует отдельный текстовый ответ с описанием карточки."""
    if not apartment.description.strip():
        return ""
    return f"<b>Описание квартиры</b>\n\n{html.escape(apartment.description[:3800])}"


def _send_album(apartment, text, channel_id):
    photos = list(apartment.photos.order_by("created_at", "pk")[:10])
    if not photos:
        result = _api("sendMessage", data={"chat_id": channel_id, "text": text, "parse_mode": "HTML"})
        return [result["message_id"]]
    media, files, handles = [], {}, []
    try:
        for index, photo in enumerate(photos):
            handle = photo.image.storage.open(photo.image.name, "rb")
            handles.append(handle)
            key = f"photo{index}"
            files[key] = (photo.image.name.rsplit("/", 1)[-1], handle, "image/jpeg")
            item = {"type": "photo", "media": f"attach://{key}"}
            if index == 0:
                item.update({"caption": text, "parse_mode": "HTML"})
            media.append(item)
        result = _api("sendMediaGroup", data={"chat_id": channel_id, "media": json.dumps(media)}, files=files)
        return [item["message_id"] for item in result]
    finally:
        for handle in handles:
            handle.close()


def _send_description(apartment, channel_id, root_message_id):
    description = _description_text(apartment)
    if not description:
        return None
    result = _api("sendMessage", data={
        "chat_id": channel_id, "text": description, "parse_mode": "HTML",
        "reply_parameters": json.dumps({"message_id": root_message_id, "allow_sending_without_reply": True}),
    })
    return result["message_id"]


def deliver_viewing_publications(limit=5):
    """Отправляет ожидающие карточки и сохраняет идентификаторы Telegram-сообщений."""
    if not settings.TG_VIEWINGS_CHANNEL_ID or not settings.TG_BOT_TOKEN:
        return 0
    delivered = 0
    stale = timezone.now() - timedelta(minutes=10)
    rows = (ViewingTelegramPublication.objects.select_related("apartment")
            .filter(channel_id=settings.TG_VIEWINGS_CHANNEL_ID)
            .filter(status__in=("pending", "failed"))
            .exclude(status="sending", updated_at__gt=stale).order_by("updated_at", "pk")[:limit])
    for row in rows:
        with transaction.atomic():
            publication = ViewingTelegramPublication.objects.select_for_update().get(pk=row.pk)
            if publication.status == "sending" and publication.updated_at > stale:
                continue
            publication.status = "sending"
            publication.attempts += 1
            publication.save(update_fields=("status", "attempts", "updated_at"))
        try:
            apartment = ViewingApartment.objects.get(pk=publication.apartment_id)
            message_ids = _send_album(apartment, _post_text(apartment), publication.channel_id)
            description_id = _send_description(apartment, publication.channel_id, message_ids[0])
            publication.message_ids = message_ids + ([description_id] if description_id else [])
            publication.status = "sent"
            publication.error = ""
            publication.published_at = timezone.now()
            publication.save(update_fields=("message_ids", "status", "error", "published_at", "updated_at"))
            delivered += 1
        except Exception as exc:  # отдельная карточка не должна останавливать worker
            publication.status = "failed"
            publication.error = f"{type(exc).__name__}: {exc}"[:2000]
            publication.save(update_fields=("status", "error", "updated_at"))
            logger.warning("Viewing Telegram publication %s failed: %s", publication.pk, type(exc).__name__)
    return delivered
