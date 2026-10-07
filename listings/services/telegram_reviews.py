"""Interactive, per-Telegram-user listing reviews."""
import html
import json
import logging
import re

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from listings.models import Listing, ListingReview, ListingReviewRevision, ReviewTag, TelegramReviewSession, Consideration

logger = logging.getLogger(__name__)

CATEGORIES = [
    (ReviewTag.Category.CONDITION, "Состояние"),
    (ReviewTag.Category.BUILDING, "Дом и окружение"),
    (ReviewTag.Category.LAYOUT, "Планировка"),
    (ReviewTag.Category.MONEY, "Деньги"),
    (ReviewTag.Category.RISK, "Риски"),
    (ReviewTag.Category.PLUS, "Плюсы"),
]


def _api(method, data):
    if not settings.TG_BOT_TOKEN:
        return {}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/{method}",
            data={k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in data.items()},
            timeout=30,
            proxies={"http": settings.TELEGRAM_PROXY_URL, "https": settings.TELEGRAM_PROXY_URL}
            if settings.TELEGRAM_PROXY_URL else None,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("Telegram %s failed: %s", method, type(exc).__name__)
        return {}


def _send(chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = keyboard
    return _api("sendMessage", data)


def _answer(callback_id, text=""):
    _api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _edit_markup(chat_id, message_id, keyboard):
    return _api("editMessageReplyMarkup", {
        "chat_id": chat_id, "message_id": message_id, "reply_markup": keyboard,
    })


def _allowed(user_id):
    configured = set(settings.TG_REVIEW_USER_IDS) or {str(settings.TG_CHAT_ID)}
    return str(user_id) in configured


def _reviewer(telegram_user_id):
    User = get_user_model()
    username = settings.TG_REVIEW_USER_MAP.get(str(telegram_user_id), settings.TG_REVIEW_DJANGO_USERNAME)
    return User.objects.filter(username=username).first() or User.objects.filter(is_staff=True).first()


def _categories_keyboard(session):
    category, label = CATEGORIES[session.category_index]
    selected = set(session.tag_codes)
    tags = ReviewTag.objects.filter(category=category, is_active=True).order_by("sort_order", "name")
    rows = []
    row = []
    for tag in tags:
        row.append({"text": ("✅ " if tag.code in selected else "⬜ ") + tag.name, "callback_data": f"review:tag:{tag.code}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "Пропустить", "callback_data": "review:next"},
        {"text": f"Дальше →", "callback_data": "review:next"},
    ])
    return rows


def _show_category(session):
    label = CATEGORIES[session.category_index][1]
    _send(session.telegram_chat_id, f"Квартира: {session.listing.title}\n\nВыберите критерии: {label}. Можно выбрать несколько или пропустить категорию.", {"inline_keyboard": _categories_keyboard(session)})


def _start_session(user_id, listing_id, channel_chat_id="", channel_message_id=None):
    listing = Listing.objects.filter(pk=listing_id, is_active=True, is_visible=True).first()
    user = _reviewer(user_id)
    if not listing or not user:
        return False
    session, _ = TelegramReviewSession.objects.update_or_create(
        telegram_user_id=str(user_id), listing=listing,
        defaults={"telegram_chat_id": str(user_id), "user": user, "state": TelegramReviewSession.State.RATING,
                  "category_index": 0, "rating": None, "decision": "", "tag_codes": [], "interest_reason": "", "deal_breaker": "", "comment": "",
                  "channel_message_id": channel_message_id, "channel_chat_id": str(channel_chat_id)},
    )
    _send(user_id, f"📝 Оценка квартиры\n{listing.title}\n\nСначала поставьте общую оценку от 1 до 10.", {"inline_keyboard": [[{"text": str(n), "callback_data": f"review:rating:{n}"} for n in range(1, 6)], [{"text": str(n), "callback_data": f"review:rating:{n}"} for n in range(6, 11)]]})
    return True


def _start(user_id, listing_id, callback):
    message = callback.get("message") or {}
    if _start_session(user_id, listing_id, message.get("chat", {}).get("id", ""), message.get("message_id")):
        _answer(callback["id"])
    else:
        _answer(callback["id"], "Квартира или пользователь не найдены")


def _save(session):
    with transaction.atomic():
        review, _ = ListingReview.objects.update_or_create(
            listing=session.listing, author=session.user,
            defaults={"rating": session.rating, "decision": session.decision, "comment": session.comment,
                      "interest_reason": session.interest_reason, "deal_breaker": session.deal_breaker},
        )
        review.tags.set(ReviewTag.objects.filter(code__in=session.tag_codes, is_active=True))
        ListingReviewRevision.objects.create(review=review, rating=review.rating, decision=review.decision,
            comment=review.comment, interest_reason=review.interest_reason, deal_breaker=review.deal_breaker,
            tag_codes=session.tag_codes)
        if review.decision == ListingReview.Decision.CONSIDER:
            Consideration.objects.get_or_create(review=review)
        else:
            Consideration.objects.filter(review=review).delete()


def _finish(session):
    _save(session)
    if session.channel_chat_id and session.channel_message_id:
        _edit_markup(session.channel_chat_id, session.channel_message_id, {"inline_keyboard": [[{"text": "✅ Оценено вами", "callback_data": "review:done"}], [{"text": "Открыть объявление", "url": session.listing.url}]]})
    _send(session.telegram_chat_id, f"✅ Оценка сохранена\n{session.listing.title}\nВаша оценка: {session.rating}/10\nРешение: {dict(ListingReview.Decision.choices)[session.decision]}")
    session.delete()


def handle_update(update):
    callback = update.get("callback_query")
    if callback:
        user_id = callback.get("from", {}).get("id")
        if not _allowed(user_id):
            _answer(callback["id"], "Доступ не настроен для этого пользователя")
            return
        data = callback.get("data", "")
        if data.startswith("review:start:"):
            _start(user_id, int(data.rsplit(":", 1)[1]), callback)
            return
        session = TelegramReviewSession.objects.filter(telegram_user_id=str(user_id)).select_related("listing", "user").first()
        if not session:
            _answer(callback["id"], "Начните оценку кнопкой в канале")
            return
        _answer(callback["id"])
        if data.startswith("review:rating:"):
            session.rating = int(data.rsplit(":", 1)[1]); session.state = TelegramReviewSession.State.TAGS; session.save()
            _show_category(session); return
        if data.startswith("review:tag:"):
            code = data.split(":", 2)[2]; selected = set(session.tag_codes)
            selected.remove(code) if code in selected else selected.add(code)
            session.tag_codes = sorted(selected); session.save(); _show_category(session); return
        if data == "review:next":
            if session.category_index + 1 < len(CATEGORIES):
                session.category_index += 1; session.save(); _show_category(session)
            else:
                session.state = TelegramReviewSession.State.INTEREST; session.save(); _send(session.telegram_chat_id, "Напишите причину интереса или отправьте «-», чтобы пропустить.")
            return
        if data.startswith("review:decision:"):
            session.decision = data.rsplit(":", 1)[1]; _finish(session)
        return
    message = update.get("message") or {}
    user_id = message.get("from", {}).get("id")
    if not user_id or not _allowed(user_id) or message.get("chat", {}).get("type") != "private":
        return
    text = (message.get("text") or "").strip()
    deep_link = re.fullmatch(r"/start(?:@\w+)?\s+review_(\d+)_(-?\d+)_(\d+)", text)
    if deep_link:
        if _start_session(user_id, int(deep_link.group(1)), deep_link.group(2), int(deep_link.group(3))):
            return
        _send(user_id, "Квартира больше недоступна для оценки.")
        return
    session = TelegramReviewSession.objects.filter(telegram_user_id=str(user_id)).select_related("listing", "user").first()
    if text in {"/cancel", "отмена"}:
        if session: session.delete()
        _send(user_id, "Текущая оценка отменена."); return
    if not session:
        _send(user_id, "Откройте объявление в канале и нажмите «Оценить квартиру»."); return
    value = "" if text == "-" else text
    if session.state == TelegramReviewSession.State.INTEREST:
        session.interest_reason = value; session.state = TelegramReviewSession.State.DEAL_BREAKER; session.save(); _send(user_id, "Что является стоп-фактором? Или отправьте «-»."); return
    if session.state == TelegramReviewSession.State.DEAL_BREAKER:
        session.deal_breaker = value; session.state = TelegramReviewSession.State.COMMENT; session.save(); _send(user_id, "Добавьте общий комментарий или отправьте «-»."); return
    if session.state == TelegramReviewSession.State.COMMENT:
        session.comment = value; session.state = TelegramReviewSession.State.DECISION; session.save()
        _send(user_id, "Выберите решение:", {"inline_keyboard": [[{"text": "✅ Готов рассмотреть", "callback_data": "review:decision:consider"}, {"text": "⛔ Не рассматривать", "callback_data": "review:decision:reject"}]]})
