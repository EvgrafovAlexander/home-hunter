import logging
from statistics import median

from django.conf import settings
from django.db import IntegrityError

from listings.models import DealAlert, Listing, SearchQuery, TelegramListingAlert
from listings.services.scan_health import send_telegram_message, send_telegram_photo

logger = logging.getLogger(__name__)
MIN_SIMILAR, MIN_ROOM_GROUP, MIN_LOCATION_GROUP = 5, 8, 12


def _listing_score(listing):
    """Return a compact score and explainable reasons for the Telegram feed."""
    position = market_position(listing)
    score = 5.0
    reasons = []
    discount = None
    if position:
        discount, reference, samples = position
        score += max(-2, min(3, discount / 5))
        if discount >= settings.TG_DEAL_MIN_DISCOUNT_PERCENT:
            reasons.append(f"на {discount}% дешевле {reference}")
        elif discount > 0:
            reasons.append(f"на {discount}% дешевле медианы рынка")
        elif discount < 0:
            reasons.append(f"на {abs(discount)}% дороже медианы рынка")
        else:
            reasons.append("цена близка к медиане рынка")
    else:
        reasons.append("недостаточно аналогов для сравнения цены")
    if listing.area is not None and listing.area >= 55:
        score += 0.5
        reasons.append("площадь от 55 м²")
    if listing.district in {"Кировский", "Ленинский", "Октябрьский", "Советский"}:
        score += 0.5
        reasons.append("предпочтительный район")
    if listing.floor is not None and listing.floor >= 7:
        score += 0.3
        reasons.append("высокий этаж")
    if listing.repair_type:
        score += 0.3
        reasons.append(f"ремонт: {listing.repair_type}")
    if listing.image_url:
        score += 0.2
        reasons.append("есть фото")
    if listing.address:
        score += 0.2
        reasons.append("указан адрес")
    return round(max(0, min(10, score)), 1), discount, reasons[:5]


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


def notify_new_listing(listing_id: int) -> bool:
    """Publish one visible new listing to the channel with an explainable score."""
    if not settings.TG_LISTING_ALERTS_ENABLED or not getattr(settings, "TG_CHANNEL_ID", ""):
        return False
    listing = Listing.objects.get(pk=listing_id)
    if not listing.is_visible or TelegramListingAlert.objects.filter(listing=listing).exists():
        return False
    score, discount, reasons = _listing_score(listing)
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
    description = " ".join((listing.title or "Квартира").split())[:180]
    text = "\n".join(filter(None, [
        heading, "", description, *facts,
        f"📍 {location}" if location else "📍 Адрес не указан",
        f"🏠 {housing}" if housing else None,
        f"⭐ Оценка Home Hunter: {score}/10", "", "Почему такая оценка:",
        *(f"• {reason}" for reason in reasons),
        "", f"Источник: {source}",
    ]))
    keyboard = {"inline_keyboard": [[
        {"text": "📝 Оценить квартиру", "url": f"{settings.PUBLIC_BASE_URL}/reviews/?listing={listing.pk}"},
    ], [{"text": "Открыть объявление", "url": listing.url}, {"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"}]]}
    sent = send_telegram_photo(listing.image_url, text, keyboard, chat_id=settings.TG_CHANNEL_ID) if listing.image_url else False
    if not sent:
        sent = send_telegram_message(text, keyboard, chat_id=settings.TG_CHANNEL_ID)
    if not sent:
        return False
    try:
        TelegramListingAlert.objects.create(listing=listing, score=score, is_deal=is_deal, reasons=reasons)
    except IntegrityError:
        logger.info("Telegram listing alert was already recorded for listing=%s", listing_id)
        return False
    return True


def market_position(listing: Listing):
    """Return (discount, reference, samples), without mixing microdistricts."""
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
    discount = round((1 - float(listing.price_per_sqm) / float(median(prices))) * 100)
    return discount, reference, len(prices)


def notify_new_deal(listing_id: int) -> bool:
    if not settings.TG_DEAL_ALERTS_ENABLED:
        return False
    listing = Listing.objects.get(pk=listing_id)
    if not listing.is_visible or DealAlert.objects.filter(listing=listing).exists():
        return False
    position = market_position(listing)
    if not position:
        return False
    discount, reference, samples = position
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
    keyboard = {"inline_keyboard": [[
        {"text": "📝 Оценить квартиру", "url": f"{settings.PUBLIC_BASE_URL}/reviews/?listing={listing.pk}"},
    ], [{"text": "Открыть объявление", "url": listing.url}, {"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"}]]}
    text = "\n".join([
        "🟢 НОВАЯ КВАРТИРА НИЖЕ РЫНКА", "",
        f"{listing.price:,} ₽ · {listing.price_per_sqm:,} ₽/м²" if listing.price and listing.price_per_sqm else (f"{listing.price:,} ₽" if listing.price else "Цена не указана"),
        facts, "", f"📍 {location}" if location else "📍 Адрес не указан", "",
        f"📉 На {discount}% ниже медианы похожих квартир", f"{reference} · {samples} аналогов", "",
        "Подходит по критериям:", criteria_text, "", "⭐ Моя оценка: не выставлена", f"Источник: {source} · добавлено сегодня",
    ])
    sent = send_telegram_photo(listing.image_url, text, keyboard) if listing.image_url else False
    if not sent:
        sent = send_telegram_message(text, keyboard)
    if not sent:
        return False
    try:
        DealAlert.objects.create(listing=listing, discount_percent=discount,
                                 market_reference=reference, sample_size=samples)
    except IntegrityError:
        logger.info("Deal alert was already recorded for listing=%s", listing_id)
        return False
    return True
