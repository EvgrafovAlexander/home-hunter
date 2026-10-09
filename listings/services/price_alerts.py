import html
import re
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError

from listings.models import Listing, TelegramListingAlert, TelegramPriceAlert
from listings.services.scan_health import edit_telegram_message, send_telegram_message


def _money(value):
    return f"{value:,}".replace(",", " ")


def _channel_message_url(chat_id, message_id):
    chat_id = str(chat_id or "")
    if chat_id.startswith("-100") and message_id:
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return ""


def _update_listing_message(alert, listing, old_price, new_price, change_percent):
    """Update the original feed post and retain a human-readable price event."""
    if not alert or not alert.message_id or not alert.message_text:
        return False
    sign = "−" if change_percent < 0 else "+"
    icon = "📉" if change_percent < 0 else "📈"
    event = (f"{icon} Цена {'снижена' if change_percent < 0 else 'повышена'}: "
             f"{_money(old_price)} ₽ → {_money(new_price)} ₽ ({sign}{abs(change_percent):.1f}%)")
    text = re.sub(
        r"(?m)^[0-9 ]+ ₽ · [0-9 ]+ ₽/м²$",
        f"{_money(new_price)} ₽ · {_money(round(new_price / listing.area))} ₽/м²" if listing.area else f"{_money(new_price)} ₽",
        alert.message_text,
        count=1,
    )
    marker = "\n\n📍"
    if event not in text:
        text = text.replace(marker, f"\n\n{event}{marker}", 1) if marker in text else f"{text}\n\n{event}"
    from listings.services.deal_alerts import _review_keyboard
    if not edit_telegram_message(
        chat_id=alert.chat_id, message_id=alert.message_id, text=text,
        message_kind=alert.message_kind or "text", parse_mode="HTML",
        reply_markup=_review_keyboard(listing, alert.message_id),
    ):
        return False
    alert.message_text = text
    alert.save(update_fields=("message_text",))
    return True


def notify_price_change(listing_id: int, old_price: int | None, new_price: int | None) -> bool:
    """Notify the channel about drops and the bot about significant increases."""
    if not settings.TG_PRICE_ALERTS_ENABLED or not old_price or not new_price or old_price == new_price:
        return False
    listing = Listing.objects.get(pk=listing_id)
    min_area = Decimal(str(getattr(settings, "TG_PRICE_ALERT_MIN_AREA", 55)))
    if not listing.is_visible or listing.area is None or listing.area < min_area:
        return False
    change_percent = (new_price - old_price) / old_price * 100
    if abs(change_percent) < settings.TG_PRICE_ALERT_MIN_PERCENT:
        return False
    target = settings.TG_CHANNEL_ID if change_percent < 0 else settings.TG_CHAT_ID
    if not target or TelegramPriceAlert.objects.filter(
        listing=listing, old_price=old_price, new_price=new_price,
    ).exists():
        return False
    direction = "📉 СНИЖЕНИЕ ЦЕНЫ" if change_percent < 0 else "📈 ПОВЫШЕНИЕ ЦЕНЫ"
    sign = "−" if change_percent < 0 else "+"
    location = " · ".join(filter(None, [listing.district, listing.microdistrict, listing.address]))
    facts = " · ".join(filter(None, [
        f"{listing.rooms} комнаты" if listing.rooms else None,
        f"{listing.area} м²" if listing.area else None,
        f"{listing.floor}/{listing.floors_total} этаж" if listing.floor and listing.floors_total else None,
    ]))
    listing_alert = TelegramListingAlert.objects.filter(listing=listing).first()
    updated_original = _update_listing_message(listing_alert, listing, old_price, new_price, change_percent)
    source_url = _channel_message_url(
        listing_alert.chat_id if listing_alert else settings.TG_CHANNEL_ID,
        listing_alert.message_id if listing_alert else None,
    ) or listing.url
    text = "\n".join(filter(None, [
        direction, "",
        f"<b>{html.escape(facts)}</b>" if facts else None,
        f"Было: {_money(old_price)} ₽",
        f"Стало: {_money(new_price)} ₽",
        f"Изменение: {sign}{abs(new_price - old_price):,} ₽ ({sign}{abs(change_percent):.1f}%)".replace(",", " "),
        "",
        f"📍 <b>{html.escape(location or 'Адрес не указан')}</b>",
        "",
        f"<a href=\"{html.escape(source_url, quote=True)}\">Открыть квартиру в канале</a>" if updated_original else
        f"<a href=\"{html.escape(listing.url, quote=True)}\">Открыть объявление</a>",
    ]))
    keyboard = {"inline_keyboard": [[
        {"text": "📝 Оценить в Telegram", "url": f"https://t.me/{settings.TG_BOT_USERNAME}?start=review_{listing.pk}_0_0"},
    ], [{"text": "📝 Оценить в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/reviews/?listing={listing.pk}"}], [{"text": "Открыть объявление", "url": listing.url}, {"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"}]]}
    if not send_telegram_message(text, keyboard, chat_id=target, parse_mode="HTML"):
        return False
    try:
        TelegramPriceAlert.objects.create(listing=listing, old_price=old_price, new_price=new_price)
    except IntegrityError:
        return False
    return True
