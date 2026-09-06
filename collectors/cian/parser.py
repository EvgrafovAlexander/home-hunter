"""Read embedded SERP JSON without executing untrusted JavaScript."""
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from collectors.base import NormalizedListing


@dataclass
class ParsedPage:
    listings: list[NormalizedListing] = field(default_factory=list)
    card_count: int = 0
    errors: int = 0
    total: int = 0
    next_url: str | None = None
    query_string: str = ""


def cian_url(url: str) -> str:
    p = urlsplit(url)
    host = p.hostname or ""
    if (p.scheme != "https" or not (host == "cian.ru" or host.endswith(".cian.ru"))
            or p.username or p.password or p.port not in (None, 443)):
        raise ValueError("Expected an HTTPS cian.ru URL")
    return url


def initial_results(soup):
    for script in soup.select("script:not([src])"):
        text = script.get_text()
        match = re.search(r"window\._cianConfig\[['\"]frontend-serp['\"]\]\s*=.*?\.concat\(\s*", text, re.S)
        if match:
            config, _ = json.JSONDecoder().raw_decode(text[match.end():])
            state = next(e["value"] for e in config if e.get("key") == "initialState")
            results = state["results"]
            if not isinstance(results.get("offers"), list):
                raise ValueError("CIAN offers is not a list")
            total = results.get("totalOffers")
            if type(total) is not int or total < 0:
                raise ValueError("Invalid CIAN totalOffers")
            return results
    raise ValueError("CIAN SERP initialState missing; not an empty search")


def integer(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Unexpected boolean")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError("Invalid nonnegative integer")
    return int(number)


def parse_page(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    results = initial_results(soup)
    cards = soup.select('[data-name="CardComponent"]')
    by_id = {}
    for card in cards:
        link = card.select_one('[data-name="TitleComponent"][href]')
        match = re.search(r"/sale/flat/(\d+)/", link["href"]) if link else None
        if match:
            by_id[match[1]] = card
    parsed = ParsedPage(card_count=len(results["offers"]), total=results["totalOffers"],
                        query_string=results.get("queryString", ""))
    if cards and len(cards) != parsed.card_count:
        parsed.errors += 1
    seen = set()
    for offer in results["offers"]:
        try:
            identifier = str(integer(offer["id"]))
            if identifier == "None" or identifier in seen:
                raise ValueError("Missing or duplicate ID")
            seen.add(identifier)
            link = urlsplit(cian_url(offer["fullUrl"]))
            if link.path != f"/sale/flat/{identifier}/":
                raise ValueError("Offer URL/id mismatch")
            card = by_id.get(identifier)
            title_node = card.select_one('[data-name="TitleComponent"]') if card else None
            # Marketing title can change independently of the apartment characteristics.
            title = offer.get("formattedFullInfo") or (title_node.get_text(" ", strip=True) if title_node else None)
            if not title:
                raise ValueError("Missing listing title")
            area = Decimal(str(offer["totalArea"])) if offer.get("totalArea") is not None else None
            if area is not None and (not area.is_finite() or not 0 < area < 100000000):
                raise ValueError("Invalid area")
            terms = offer.get("bargainTerms") or {}
            price = integer(terms.get("priceRur"))
            if price is None and terms.get("currency") == "rur":
                price = integer(terms.get("price"))
            per_sqm = None
            if card:
                price_node = card.select_one('[data-mark="MainPrice"]')
                if price_node and price is not None:
                    dom_price = re.sub(r"\D", "", price_node.get_text())
                    if dom_price and int(dom_price) != price:
                        raise ValueError("DOM/JSON price mismatch")
                node = card.select_one('[data-mark="PriceInfo"]')
                match = re.search(r"([\d\s\xa0]+)\s*₽/м²", node.get_text()) if node else None
                if match:
                    per_sqm = int(re.sub(r"\D", "", match[1]))
            address = (offer.get("geo") or {}).get("address") or []
            photos = offer.get("photos") or []
            photo = next((p for p in photos if p.get("isDefault")), photos[0] if photos else {})
            parsed.listings.append(NormalizedListing(
                source="cian", external_id=identifier, url=urlunsplit(link._replace(query="", fragment="")),
                title=title[:1000], price=price, price_per_sqm=per_sqm,
                rooms=integer(offer.get("roomsCount")), area=area,
                floor=integer(offer.get("floorNumber")),
                floors_total=integer((offer.get("building") or {}).get("floorsCount")),
                address=", ".join(a["fullName"] for a in address if a.get("fullName")) or None,
                district=next((a.get("name") for a in address if a.get("type") == "raion"), None),
                description=offer.get("description"), published_text=(offer.get("added") or "")[:255] or None,
                image_url=photo.get("fullUrl"),
            ))
        except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
            parsed.errors += 1
    if by_id and set(by_id) != seen:
        parsed.errors += 1
    next_links = [a for a in soup.select("a[href]") if a.get_text(" ", strip=True) == "Дальше"]
    if next_links:
        parsed.next_url = cian_url(urljoin(url, next_links[0]["href"]))
    if not parsed.card_count and (parsed.total or cards or parsed.next_url):
        raise ValueError("Empty CIAN offers contradict page metadata")
    return parsed
