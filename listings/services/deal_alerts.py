import logging
from statistics import median

from django.conf import settings
from django.db import IntegrityError

from listings.models import DealAlert, Listing, SearchQuery
from listings.services.scan_health import send_telegram_message, send_telegram_photo

logger = logging.getLogger(__name__)
MIN_SIMILAR, MIN_ROOM_GROUP, MIN_LOCATION_GROUP = 5, 8, 12


def market_position(listing: Listing):
    """Return (discount, reference, samples), without mixing microdistricts."""
    if (listing.price_per_sqm is None or not listing.district
            or listing.rooms is None or listing.area is None):
        return None
    rows = (Listing.objects.filter(is_active=True, price_per_sqm__isnull=False, district=listing.district)
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
    text = "\n".join([
        "🟢 Новое выгодное объявление",
        f"На {discount}% ниже рынка · {reference}, {samples} аналогов",
        f"{listing.price:,} ₽" if listing.price else "Цена не указана",
        facts, location, source, listing.url,
    ])
    sent = send_telegram_photo(listing.image_url, text) if listing.image_url else False
    if not sent:
        sent = send_telegram_message(text)
    if not sent:
        return False
    try:
        DealAlert.objects.create(listing=listing, discount_percent=discount,
                                 market_reference=reference, sample_size=samples)
    except IntegrityError:
        logger.info("Deal alert was already recorded for listing=%s", listing_id)
        return False
    return True
