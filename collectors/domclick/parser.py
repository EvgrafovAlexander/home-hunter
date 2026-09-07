import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from collectors.base import NormalizedListing
from listings.services.enrichment import district_from_address

CARD_PATH = re.compile(r"/card/([^/?#]+)")
TITLE = re.compile(r"(?P<rooms>\d+)-комн\.\s*квартира\s*(?P<area>\d+(?:[,.]\d+)?)\s*м[²2]\s*(?P<floor>\d+)\s*/\s*(?P<floors>\d+)\s*эт", re.I)
MONEY = re.compile(r"(\d[\d\s\u00a0\u202f]*)\s*₽")
RELATIVE_TIME = re.compile(r"(?:сегодня|вчера|(?:\d+\s+)?(?:минут[аы]?|час(?:а|ов)?|дн(?:я|ей))\s+назад)", re.I)


@dataclass
class ParsedPage:
    listings: list[NormalizedListing] = field(default_factory=list)
    card_count: int = 0
    errors: int = 0


def _text(node: Tag) -> str:
    return node.get_text(" ", strip=True).replace("\xa0", " ")


def _money(value: str) -> int | None:
    match = MONEY.search(value)
    return int(re.sub(r"\D", "", match.group(1))) if match else None


def _card_for(link: Tag) -> Tag:
    """Find the smallest enclosing element containing this offer only."""
    card = link.parent
    while isinstance(card, Tag) and card.parent:
        identifiers = {CARD_PATH.search(a.get("href", "")).group(1) for a in card.select("a[href]")
                       if CARD_PATH.search(a.get("href", ""))}
        if len(identifiers) != 1:
            break
        parent = card.parent
        parent_ids = {CARD_PATH.search(a.get("href", "")).group(1) for a in parent.select("a[href]")
                      if CARD_PATH.search(a.get("href", ""))}
        if len(parent_ids) != 1:
            break
        card = parent
    if not isinstance(card, Tag):
        raise ValueError("Offer card has no container")
    return card


def parse_page(html: str, page_url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for link in soup.select("a[href]"):
        match = CARD_PATH.search(link["href"])
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            links.append(link)
    result = ParsedPage(card_count=len(links))
    for link in links:
        try:
            match = CARD_PATH.search(link["href"])
            assert match
            external_id = match.group(1)
            url = urljoin(page_url, link["href"])
            parsed_url = urlsplit(url)
            if parsed_url.scheme != "https" or not (parsed_url.hostname or "").endswith("domclick.ru"):
                raise ValueError("Invalid Domclick offer URL")
            title = _text(link)
            characteristics = TITLE.search(title)
            if not characteristics:
                raise ValueError("Unknown Domclick listing title")
            values = characteristics.groupdict()
            card = _card_for(link)
            text = _text(card)
            prices = MONEY.findall(text)
            if not prices:
                raise ValueError("Missing Domclick price")
            price = int(re.sub(r"\D", "", prices[0]))
            per_sqm = int(re.sub(r"\D", "", prices[1])) if len(prices) > 1 and "/м" in text else None
            images = card.select("img[src], img[data-src]")
            image_url = next((image.get("src") or image.get("data-src") for image in images), None)
            # The first text immediately after the title is the address in the current SERP.
            title_parent = link.parent
            address = None
            for sibling in title_parent.find_all_next(string=True, limit=8):
                candidate = str(sibling).strip()
                if candidate and candidate not in title and "₽" not in candidate:
                    address = candidate
                    break
            published_match = RELATIVE_TIME.search(text)
            published = published_match.group(0) if published_match else None
            stable_description = RELATIVE_TIME.sub("", text).strip()
            result.listings.append(NormalizedListing(
                source="domclick", external_id=external_id,
                url=urlunsplit(parsed_url._replace(query="", fragment="")), title=title,
                price=price, price_per_sqm=per_sqm, rooms=int(values["rooms"]),
                area=Decimal(values["area"].replace(",", ".")), floor=int(values["floor"]),
                floors_total=int(values["floors"]), address=address,
                district=district_from_address(address), description=stable_description or None,
                published_text=published, image_url=urljoin(page_url, image_url) if image_url else None,
            ))
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            result.errors += 1
    return result
