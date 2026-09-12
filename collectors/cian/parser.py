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


@dataclass
class CianDetail:
    status: str
    listing: NormalizedListing | None = None
    photo_ids: list[int] = field(default_factory=list)
    photos: list[dict] = field(default_factory=list)
    edited_at: str | None = None
    offer_data: dict = field(default_factory=dict)
    attributes: dict = field(default_factory=dict)
    latitude: Decimal | None = None
    longitude: Decimal | None = None


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


def building_year(building):
    for key in ("buildYear", "yearBuilt", "year", "constructionYear"):
        try:
            year = integer((building or {}).get(key))
        except (ValueError, TypeError, ArithmeticError):
            continue
        if year is not None and 1800 <= year <= 2100:
            return year
    return None


def detail_attributes(offer_data):
    offer = offer_data.get("offer") or {}
    building = offer.get("building") or {}
    parking = building.get("parking") or {}

    def dec(value, maximum=100000):
        try:
            value = Decimal(str(value))
            return value if value.is_finite() and 0 < value < maximum else None
        except (ValueError, TypeError, ArithmeticError):
            return None

    def num(value):
        try:
            return integer(value)
        except (ValueError, TypeError, ArithmeticError):
            return None

    def text(value):
        return value[:100] if isinstance(value, str) and value else None

    def flag(value):
        return value if isinstance(value, bool) else None

    return {
        "kitchen_area": dec(offer.get("kitchenArea")),
        "living_area": dec(offer.get("livingArea")),
        "ceiling_height": dec(building.get("ceilingHeight"), 20),
        "bathrooms_combined": num(offer.get("combinedWcsCount")),
        "bathrooms_separate": num(offer.get("separateWcsCount")),
        "balconies_count": num(offer.get("balconiesCount")),
        "loggias_count": num(offer.get("loggiasCount")),
        "repair_type": text(offer.get("repairType")),
        "windows_view_type": text(offer.get("windowsViewType")),
        "has_furniture": flag(offer.get("hasFurniture")),
        "passenger_lifts_count": num(offer.get("passengerLiftsCount")),
        "cargo_lifts_count": num(offer.get("cargoLiftsCount")),
        "has_ramp": flag(offer.get("hasRamp")),
        "building_material_type": text(building.get("materialType")),
        "parking_type": text(parking.get("type")),
        "has_garbage_chute": flag(building.get("hasGarbageChute")),
    }


def detail_coordinates(offer_data):
    geo = (offer_data.get("offer") or {}).get("geo") or {}
    try:
        lat = Decimal(str(geo.get("lat", geo.get("latitude"))))
        lon = Decimal(str(geo.get("lon", geo.get("longitude"))))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None
        return lat, lon
    except (ValueError, TypeError, ArithmeticError):
        return None, None


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
            building = offer.get("building") or {}
            parsed.listings.append(NormalizedListing(
                source="cian", external_id=identifier, url=urlunsplit(link._replace(query="", fragment="")),
                title=title[:1000], price=price, price_per_sqm=per_sqm,
                rooms=integer(offer.get("roomsCount")), area=area,
                floor=integer(offer.get("floorNumber")),
                floors_total=integer(building.get("floorsCount")), built_year=building_year(building),
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


def parse_detail_page(html: str, url: str) -> CianDetail:
    """Parse the offer-card state; never infer availability from an error page."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select("script:not([src])"):
        text = script.get_text()
        match = re.search(r"window\._cianConfig\[['\"]frontend-offer-card['\"]\]\s*=.*?\.concat\(\s*", text, re.S)
        if not match:
            continue
        config, _ = json.JSONDecoder().raw_decode(text[match.end():])
        state = next((e.get("value") for e in config if e.get("key") == "defaultState"), None)
        offer_data = (state or {}).get("offerData") or {}
        if not isinstance(offer_data, dict):
            raise ValueError("CIAN offerData is not an object")
        offer = offer_data.get("offer") or {}
        identifier = str(integer(offer.get("id")))
        expected = re.search(r"/sale/flat/(\d+)/", urlsplit(url).path)
        if not expected or identifier != expected.group(1):
            raise ValueError("CIAN detail URL/id mismatch")
        status = offer.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("CIAN detail status missing")
        photos = offer.get("photos") or []
        photo_data = [{
            "id": identifier,
            "full_url": photo.get("fullUrl"),
            "thumbnail_url": photo.get("thumbnailUrl"),
            "preview_url": photo.get("thumbnail2Url"),
            "mini_url": photo.get("miniUrl"),
        } for photo in photos if (identifier := integer(photo.get("id"))) is not None and photo.get("fullUrl")]
        photo_ids = [photo["id"] for photo in photo_data]
        if status != "published":
            return CianDetail(
            status=status, photo_ids=photo_ids, photos=photo_data,
            edited_at=offer.get("editDate"), offer_data=offer_data, attributes=detail_attributes(offer_data),
            latitude=detail_coordinates(offer_data)[0], longitude=detail_coordinates(offer_data)[1],
        )
        building = offer.get("building") or {}
        geo = offer.get("geo") or {}
        address = geo.get("address") or []
        price = integer(offer.get("priceTotalRur"))
        area = Decimal(str(offer["totalArea"])) if offer.get("totalArea") is not None else None
        image = next((photo.get("fullUrl") for photo in photos if photo.get("isDefault")),
                     photos[0].get("fullUrl") if photos else None)
        title = offer_data.get("pageHelmetData", {}).get("title")
        listing = NormalizedListing(
            # A detail response can legitimately omit pageHelmetData.title.  Do
            # not manufacture a technical title here: the saved search card is
            # the authoritative source for this display field.
            source="cian", external_id=identifier, url=url, title=(title or "")[:1000],
            price=price, rooms=integer(offer.get("roomsCount")), area=area,
            floor=integer(offer.get("floorNumber")), floors_total=integer(building.get("floorsCount")),
            built_year=building_year(building),
            address=", ".join(a.get("fullName", "") for a in address if a.get("fullName")) or None,
            district=next((a.get("name") for a in address if a.get("type") == "raion"), None),
            description=offer.get("description"), published_text=offer.get("humanizedEditDate"), image_url=image,
        )
        return CianDetail(
            status=status, listing=listing, photo_ids=photo_ids, photos=photo_data,
            edited_at=offer.get("editDate"), offer_data=offer_data, attributes=detail_attributes(offer_data),
            latitude=detail_coordinates(offer_data)[0], longitude=detail_coordinates(offer_data)[1],
        )
    raise ValueError("CIAN offer-card defaultState missing")
