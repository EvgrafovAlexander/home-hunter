import logging
import re
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from collectors.base import NormalizedListing
from listings.services.enrichment import district_from_address
from . import selectors as sel
from .types import ParsedPage

logger = logging.getLogger(__name__)
TITLE_PATTERN = re.compile(
    r"(?P<rooms>\d+)-к\.\s*квартира,\s*(?P<area>\d+(?:[,.]\d+)?)\s*м[²2],"
    r"\s*(?P<floor>\d+)\s*/\s*(?P<floors_total>\d+)\s*эт\.", re.I
)


def parse_title(title: str) -> dict:
    match = TITLE_PATTERN.search(title)
    if not match:
        logger.warning("Unknown title format: %s", title[:200])
        return dict(rooms=None, area=None, floor=None, floors_total=None)
    values = match.groupdict()
    return {
        "rooms": int(values["rooms"]),
        "area": Decimal(values["area"].replace(",", ".")),
        "floor": int(values["floor"]),
        "floors_total": int(values["floors_total"]),
    }


def text_at(card: Tag, selector: str) -> str | None:
    node = card.select_one(selector)
    return node.get_text(" ", strip=True) or None if node else None


def parse_money(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d[\d\s\u00a0\u202f]*", value)
    return int(re.sub(r"\s", "", match.group())) if match else None


def parse_card(card: Tag) -> NormalizedListing:
    external_id = card.get("data-item-id")
    title_node = card.select_one(sel.TITLE)
    if not external_id or not title_node:
        raise ValueError("Card missing external_id or title")
    title = title_node.get_text(" ", strip=True)
    href = title_node.get("href")
    if not title or not href:
        raise ValueError("Card missing title text or href")
    url = urljoin("https://www.avito.ru", href)
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Invalid listing URL")
    price_meta = card.select_one(sel.PRICE_META)
    price = parse_money(price_meta.get("content") if price_meta else text_at(card, sel.PRICE))
    visible_text = card.get_text(" ", strip=True)
    sqm = re.search(r"(\d[\d\s\u00a0\u202f]*)\s*₽\s*/\s*м[²2]", visible_text)
    image = card.select_one(sel.IMAGE)
    image_url = next((image.get(attr) for attr in ("content", "src", "href") if image.get(attr)), None) if image else None
    description = text_at(card, sel.DESCRIPTION)
    if not description:
        # Relative dates must not cause a new snapshot on each observation.
        stable = BeautifulSoup(str(card), "html.parser")
        for node in stable.select(sel.DATE):
            node.decompose()
        description = stable.get_text(" ", strip=True)
    address = text_at(card, sel.ADDRESS)
    district = text_at(card, sel.DISTRICT) or district_from_address(address)
    return NormalizedListing(
        source="avito", external_id=str(external_id), title=title, url=url, price=price,
        price_per_sqm=parse_money(sqm.group(1)) if sqm else None,
        address=address, district=district,
        description=description, published_text=text_at(card, sel.DATE),
        image_url=urljoin("https://www.avito.ru", image_url) if image_url else None,
        raw_data={"visible_text": visible_text}, **parse_title(title),
    )


def parse_page(html: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(sel.ITEM)
    result = ParsedPage(card_count=len(cards))
    for card in cards:
        try:
            result.listings.append(parse_card(card))
        except (ValueError, TypeError, AttributeError, OverflowError) as exc:
            result.errors += 1
            logger.warning("Skipping damaged card id=%s: %s", card.get("data-item-id"), exc)
    return result
