import html
import logging
import re
from statistics import median

from django.conf import settings
from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.utils import timezone

from listings.models import DealAlert, Listing, SearchQuery, TelegramListingAlert
from listings.services.scan_health import (edit_telegram_message, edit_telegram_reply_markup, send_telegram_message,
                                           send_telegram_message_result, send_telegram_photo,
                                           send_telegram_photo_result)

logger = logging.getLogger(__name__)
MIN_SIMILAR, MIN_ROOM_GROUP, MIN_LOCATION_GROUP = 5, 8, 12


def _review_keyboard(listing, channel_message_id):
    link = f"https://t.me/{settings.TG_BOT_USERNAME}?start=review_{listing.pk}_{settings.TG_CHANNEL_ID}_{channel_message_id}"
    return {"inline_keyboard": [
        [{"text": "📝 Оценить в Telegram", "url": link}],
        [{"text": "📝 Оценить в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/reviews/?listing={listing.pk}"}],
        [{"text": "Открыть объявление", "url": listing.url}, {"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"}],
    ]}


def _send_reviewable_listing(listing, text):
    """Publish first, then add a deep link containing its message id."""
    initial_keyboard = {"inline_keyboard": [[
        {"text": "Открыть объявление", "url": listing.url},
        {"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"},
    ]]}
    result = send_telegram_photo_result(listing.image_url, text, initial_keyboard, chat_id=settings.TG_CHANNEL_ID, parse_mode="HTML") if listing.image_url else None
    if not result:
        result = send_telegram_message_result(text, initial_keyboard, chat_id=settings.TG_CHANNEL_ID, parse_mode="HTML")
    if not result:
        return None
    if not settings.TG_BOT_USERNAME:
        logger.warning("TG_BOT_USERNAME is not configured; review deep link was not added")
        return result
    edit_telegram_reply_markup(chat_id=settings.TG_CHANNEL_ID, message_id=result["message_id"], reply_markup=_review_keyboard(listing, result["message_id"]))
    return result


def _telegram_fit_score(listing):
    """Calculate the same personal Fit score as the web feed, when coordinates exist."""
    if listing.latitude is None or listing.longitude is None:
        return None, []
    user = get_user_model().objects.filter(username=settings.TG_REVIEW_DJANGO_USERNAME).first()
    if not user:
        return None, []
    from listings.services.personal_filters import annotate_personal_filter
    from listings.views import add_listing_score, scoring_preference_for
    annotate_personal_filter([listing], user)
    add_listing_score([listing], scoring_preference_for(user))
    return listing.score, getattr(listing, "score_reasons", [])


def _telegram_personal_details(listing):
    """Return concise, preference-driven explanations for the final score."""
    user = get_user_model().objects.filter(username=settings.TG_REVIEW_DJANGO_USERNAME).first()
    if not user:
        return []
    from listings.views import scoring_preference_for
    preference = scoring_preference_for(user)
    details = []
    tier_labels = {"A": "Идеальная зона", "B": "Хорошая зона", "C": "Компромиссная зона", "D": "Не рассматривается"}
    tier = getattr(listing, "geo_zone_tier", None)
    if tier in tier_labels:
        details.append(("plus" if tier in {"A", "B"} else "minus", tier_labels[tier]))
    if preference.preferred_rooms:
        rooms = {int(value) for value in preference.preferred_rooms if str(value).isdigit()}
        details.append(("plus" if listing.rooms in rooms else "minus", "Комнатность соответствует цели" if listing.rooms in rooms else "Комнатность отличается от цели"))
    if preference.min_area is not None or preference.max_area is not None:
        fits = listing.area is not None and (preference.min_area is None or listing.area >= preference.min_area) and (preference.max_area is None or listing.area <= preference.max_area)
        details.append(("plus" if fits else "minus", "Площадь соответствует диапазону" if fits else "Площадь вне заданного диапазона"))
    if preference.floor_min is not None or preference.floor_max is not None:
        fits = listing.floor is not None and (preference.floor_min is None or listing.floor >= preference.floor_min) and (preference.floor_max is None or listing.floor <= preference.floor_max)
        details.append(("plus" if fits else "minus", "Этаж соответствует предпочтению" if fits else "Этаж ниже или выше предпочтения"))
    if listing.repair_type in preference.preferred_repair_types:
        details.append(("plus", "Состояние соответствует предпочтению"))
    return details[:5]


def refresh_telegram_listing_score(listing_id: int) -> bool:
    """Replace the temporary score in the already published message after geocoding."""
    try:
        alert = TelegramListingAlert.objects.select_related("listing").get(listing_id=listing_id)
    except TelegramListingAlert.DoesNotExist:
        return False
    score, reasons = _telegram_fit_score(alert.listing)
    if score is None or not alert.message_id or not alert.message_text:
        return False
    text = re.sub(r"Оценка Home Hunter: [^<\n]+", f"Оценка Home Hunter: {score}/100", alert.message_text, count=1)
    personal_details = _telegram_personal_details(alert.listing)
    if personal_details:
        text = re.sub(r"\n\n<b>По вашим настройкам:</b>\n.*?\n\nИсточник:", "\nИсточник:", text, count=1, flags=re.S)
        blocks = ["<b>По вашим настройкам:</b>"]
        blocks.extend(("✅ " if kind == "plus" else "⚠️ ") + html.escape(label) for kind, label in personal_details)
        personal_block = "\n".join(blocks)
        text = text.replace("\nИсточник:", f"\n\n{personal_block}\n\nИсточник:", 1)
    if not edit_telegram_message(chat_id=alert.chat_id or settings.TG_CHANNEL_ID, message_id=alert.message_id,
                                 text=text, message_kind=alert.message_kind or "text", parse_mode="HTML"):
        return False
    alert.score = score
    alert.reasons = reasons
    alert.message_text = text
    alert.score_ready = True
    alert.score_updated_at = timezone.now()
    alert.save(update_fields=("score", "reasons", "message_text", "score_ready", "score_updated_at"))
    return True


def _listing_score(listing):
    """Return a compact score and explainable reasons for the Telegram feed."""
    position = market_position_details(listing)
    score = 5.0
    reasons = []

    def add(kind, text):
        reasons.append((kind, text))
    discount = None
    if position:
        discount, reference, samples, _median_price = position
        score += max(-2, min(3, discount / 5))
        if discount >= settings.TG_DEAL_MIN_DISCOUNT_PERCENT:
            add("plus", f"На {discount}% дешевле {reference}")
        elif discount > 0:
            add("plus", f"На {discount}% дешевле медианы рынка")
        elif discount < 0:
            add("minus", f"На {abs(discount)}% дороже медианы рынка")
        else:
            add("info", "Цена близка к медиане рынка")
    else:
        add("info", "Недостаточно аналогов для сравнения цены")
    if listing.repair_type and listing.repair_type.lower() not in {"без ремонта", "требует ремонта"}:
        score += 0.3
        add("plus", f"Ремонт: {listing.repair_type}")
    if listing.kitchen_area and listing.area and float(listing.kitchen_area) / float(listing.area) >= .20:
        score += 0.3
        add("plus", f"Кухня {_area(listing.kitchen_area)} м² — выше обычной доли")
    return round(max(0, min(10, score)), 1), discount, reasons[:8]


def _money(value):
    return f"{value:,}".replace(",", " ") if value else None


def _area(value):
    return f"{value:.1f}".replace(".", ",") if value is not None else None


def _rooms(value):
    if value is None:
        return None
    if value == 1:
        return "1 комната"
    if value in {2, 3, 4}:
        return f"{value} комнаты"
    return f"{value} комнат"


def _brief_description(title):
    """Remove source-generated room/area/floor fragments from the title."""
    value = " ".join((title or "").replace("\xa0", " ").split())
    value = re.sub(r"\b\d+\s*[-‑–—]?\s*(?:комн(?:ат(?:ная|ы|ая))?\.?\s*(?:кв\.?)?|к\.)", " ", value, flags=re.I)
    value = re.sub(r"\b\d+(?:[,.]\d+)?\s*м(?:²|2)\b", " ", value, flags=re.I)
    value = re.sub(r"\b\d+\s*/\s*\d+\s*(?:этаж|эт\.?)\b", " ", value, flags=re.I)
    value = re.sub(r"\s*[·•,;|.]+\s*", " ", value)
    value = " ".join(value.split()).strip(" -–—")
    if value.lower() in {"квартира", "кв"}:
        return None
    return html.escape(value[:180]) if len(value) >= 4 else None


def notify_new_listing(listing_id: int) -> bool:
    """Publish one visible new listing to the channel with an explainable score."""
    if not settings.TG_LISTING_ALERTS_ENABLED or not getattr(settings, "TG_CHANNEL_ID", ""):
        return False
    listing = Listing.objects.get(pk=listing_id)
    if not listing.is_visible or TelegramListingAlert.objects.filter(listing=listing).exists():
        return False
    score, discount, reasons = _listing_score(listing)
    market = market_position_details(listing)
    source = SearchQuery.Source(listing.source).label
    facts = [
        " · ".join(filter(None, [_rooms(listing.rooms), f"{_area(listing.area)} м²" if listing.area else None])),
        " · ".join(filter(None, [
            f"{_money(listing.price)} ₽" if listing.price else "Цена не указана",
            f"{_money(listing.price_per_sqm)} ₽/м²" if listing.price_per_sqm else None,
        ])),
    ]
    floor = None
    if listing.floor is not None:
        floor = f"{listing.floor}/{listing.floors_total} этаж" if listing.floors_total else f"{listing.floor} этаж"
    details = " · ".join(filter(None, [
        floor,
        f"кухня {_area(listing.kitchen_area)} м²" if listing.kitchen_area else None,
    ]))
    if details:
        facts.append(details)
    location = " · ".join(filter(None, [listing.district, listing.microdistrict, listing.address]))
    housing = " · ".join(filter(None, [
        listing.building_material_type,
        listing.repair_type,
        "балкон/лоджия" if (listing.balconies_count or listing.loggias_count) else None,
        "мебель" if listing.has_furniture else None,
    ]))
    is_deal = discount is not None and discount >= settings.TG_DEAL_MIN_DISCOUNT_PERCENT
    heading = "🟢 ВЫГОДНЫЙ ВАРИАНТ" if is_deal else "🆕 НОВАЯ КВАРТИРА"
    description = _brief_description(listing.title)
    escaped_location = html.escape(location) if location else "Адрес не указан"
    escaped_housing = html.escape(housing) if housing else ""
    pluses = [text for kind, text in reasons if kind == "plus"]
    minuses = [text for kind, text in reasons if kind == "minus"]
    infos = [text for kind, text in reasons if kind == "info"]
    market_lines = []
    if market:
        market_discount, market_reference, market_samples, market_median = market
        place = "микрорайона" if listing.microdistrict else "района"
        direction = "ниже" if market_discount > 0 else "выше" if market_discount < 0 else "на уровне"
        market_lines = [
            f"📊 <b>Цена {direction} медианы {place} на {abs(market_discount)}%</b>",
            f"Медиана: {_money(market_median)} ₽/м² · аналогов: {market_samples}",
        ]
    else:
        market_lines = [
            "📊 <b>Медиану сравнить не удалось</b>",
            "Недостаточно похожих квартир для расчёта",
        ]
    reason_blocks = []
    if pluses:
        reason_blocks.extend(["<b>Плюсы:</b>", *(f"✅ {html.escape(reason)}" for reason in pluses[:5])])
    if minuses:
        reason_blocks.extend(["", "<b>Минусы:</b>", *(f"⚠️ {html.escape(reason)}" for reason in minuses[:5])])
    if infos:
        reason_blocks.extend(["", "<b>Что ещё учтено:</b>", *(f"ℹ️ {html.escape(reason)}" for reason in infos[:3])])
    lines = [heading, ""]
    if description:
        lines.extend([description, ""])
    lines.extend([*facts, "", f"📍 <b>{escaped_location}</b>"])
    if escaped_housing:
        lines.extend(["", f"🏠 {escaped_housing}"])
    lines.extend(["", *market_lines, "", "🔄 <b>Оценка Home Hunter: пересчитывается</b>", ""])
    lines.extend(reason_blocks)
    lines.extend(["", f"Источник: {html.escape(source)}"])
    text = "\n".join(line for line in lines if line is not None)
    result = _send_reviewable_listing(listing, text)
    if not result:
        return False
    try:
        TelegramListingAlert.objects.create(
            listing=listing, score=None, is_deal=is_deal, reasons=reasons,
            chat_id=str(settings.TG_CHANNEL_ID), message_id=result.get("message_id"),
            message_kind="photo" if listing.image_url else "text", message_text=text,
        )
    except IntegrityError:
        logger.info("Telegram listing alert was already recorded for listing=%s", listing_id)
        return False
    refresh_telegram_listing_score(listing.pk)
    return True


def market_position(listing: Listing):
    details = market_position_details(listing)
    return details[:3] if details else None


def market_position_details(listing: Listing):
    """Return (discount, reference, samples, median_price), without mixing locations."""
    if (listing.price_per_sqm is None or not listing.district
            or listing.rooms is None or listing.area is None):
        return None
    rows = (Listing.objects.filter(is_active=True, is_visible=True, price_per_sqm__isnull=False, district=listing.district)
            .exclude(pk=listing.pk).exclude(rooms__isnull=True).exclude(area__isnull=True)
            .values("microdistrict", "rooms", "area", "price_per_sqm"))
    if listing.microdistrict:
        rows = [row for row in rows if row["microdistrict"] == listing.microdistrict]
        location = "микрорайону"
    else:
        rows = [row for row in rows if not row["microdistrict"]]
        location = "району"
    same_room = [row for row in rows if row["rooms"] == listing.rooms]
    similar = [row["price_per_sqm"] for row in same_room if abs(float(row["area"]) - float(listing.area)) <= 10]
    if len(similar) >= MIN_SIMILAR:
        prices, reference = similar, "похожим квартирам"
    elif len(same_room) >= MIN_ROOM_GROUP:
        prices, reference = [row["price_per_sqm"] for row in same_room], f"{location} и комнатности"
    elif len(rows) >= MIN_LOCATION_GROUP:
        prices, reference = [row["price_per_sqm"] for row in rows], location
    else:
        return None
    median_price = round(float(median(prices)))
    discount = round((1 - float(listing.price_per_sqm) / median_price) * 100)
    return discount, reference, len(prices), median_price


def notify_new_deal(listing_id: int) -> bool:
    if not settings.TG_DEAL_ALERTS_ENABLED:
        return False
    listing = Listing.objects.get(pk=listing_id)
    if not listing.is_visible or DealAlert.objects.filter(listing=listing).exists():
        return False
    position = market_position_details(listing)
    if not position:
        return False
    discount, reference, samples, _median_price = position
    if discount < settings.TG_DEAL_MIN_DISCOUNT_PERCENT:
        return False
    source = SearchQuery.Source(listing.source).label
    facts = " · ".join(filter(None, [
        f"{listing.rooms} комн." if listing.rooms is not None else "",
        f"{listing.area} м²" if listing.area else "",
        f"{listing.price_per_sqm:,} ₽/м²" if listing.price_per_sqm else "",
    ]))
    location = " · ".join(filter(None, [listing.district, listing.microdistrict, listing.address]))
    criteria = []
    if listing.area is not None and listing.area >= 55:
        criteria.append("площадь подходит")
    if listing.floor is not None and listing.floor >= 7:
        criteria.append("этаж подходит")
    if listing.district in {"Кировский", "Ленинский", "Октябрьский", "Советский"}:
        criteria.append("предпочтительный район")
    if listing.repair_type:
        criteria.append(f"ремонт: {listing.repair_type}")
    criteria_text = "\n".join(f"✓ {item}" for item in criteria) or "• критерии требуют проверки"
    text = "\n".join([
        "🟢 НОВАЯ КВАРТИРА НИЖЕ РЫНКА", "",
        f"{listing.price:,} ₽ · {listing.price_per_sqm:,} ₽/м²" if listing.price and listing.price_per_sqm else (f"{listing.price:,} ₽" if listing.price else "Цена не указана"),
        facts, "", f"📍 {location}" if location else "📍 Адрес не указан", "",
        f"📉 На {discount}% ниже медианы похожих квартир", f"{reference} · {samples} аналогов", "",
        "Подходит по критериям:", criteria_text, "", "⭐ Моя оценка: не выставлена", f"Источник: {source} · добавлено сегодня",
    ])
    sent = _send_reviewable_listing(listing, text)
    if not sent:
        return False
    try:
        DealAlert.objects.create(listing=listing, discount_percent=discount,
                                 market_reference=reference, sample_size=samples)
    except IntegrityError:
        logger.info("Deal alert was already recorded for listing=%s", listing_id)
        return False
    return True
