import html

from django.conf import settings
from django.db import IntegrityError

from listings.models import Listing, TelegramPriceAlert
from listings.services.scan_health import send_telegram_message


def _money(value):
    return f"{value:,}".replace(",", " ")


def notify_price_change(listing_id: int, old_price: int | None, new_price: int | None) -> bool:
    """Notify the channel about drops and the bot about significant increases."""
    if not settings.TG_PRICE_ALERTS_ENABLED or not old_price or not new_price or old_price == new_price:
        return False
    listing = Listing.objects.get(pk=listing_id)
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
    text = "\n".join(filter(None, [
        direction, "",
        f"<b>{html.escape(facts)}</b>" if facts else None,
        f"Было: {_money(old_price)} ₽",
        f"Стало: {_money(new_price)} ₽",
        f"Изменение: {sign}{abs(new_price - old_price):,} ₽ ({sign}{abs(change_percent):.1f}%)".replace(",", " "),
        "",
        f"📍 <b>{html.escape(location or 'Адрес не указан')}</b>",
        "",
        f"<a href=\"{html.escape(listing.url, quote=True)}\">Открыть объявление</a>",
    ]))
    keyboard = {"inline_keyboard": [[
        {"text": "📝 Оценить квартиру", "url": f"{settings.PUBLIC_BASE_URL}/reviews/?listing={listing.pk}"},
    ], [{"text": "Открыть в Home Hunter", "url": f"{settings.PUBLIC_BASE_URL}/listings/{listing.pk}/"}]]}
    if not send_telegram_message(text, keyboard, chat_id=target, parse_mode="HTML"):
        return False
    try:
        TelegramPriceAlert.objects.create(listing=listing, old_price=old_price, new_price=new_price)
    except IntegrityError:
        return False
    return True
