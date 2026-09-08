"""Resolve parsed locations against the editable Ufa location directory."""

from dataclasses import dataclass
import re

from listings.models import District, Microdistrict, StreetAssignment


def normalized_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold().replace("ё", "е"))


def _matches(value: str | None, name: str, aliases: list[str]) -> bool:
    candidate = normalized_text(value)
    return bool(candidate) and candidate in {normalized_text(name), *(normalized_text(alias) for alias in aliases)}


def address_street_and_house(address: str | None) -> tuple[str, int | None]:
    """Extract a stable street key and the first house number from an address."""
    components = [part.strip() for part in (address or "").split(",") if part.strip()]
    if not components:
        return "", None
    house = None
    street_parts = []
    for part in components:
        number = re.match(r"^(\d+)(?:[\s/].*)?$", part)
        if number and house is None:
            house = int(number.group(1))
            break
        street_parts.append(part)
    street = normalized_text(", ".join(street_parts or components))
    # Sources alternate between “Ленина улица” and “ул. Ленина”; rules should
    # not depend on that presentation detail.
    street = re.sub(r"\b(?:ул(?:ица)?\.?|улица)", "", street)
    street = re.sub(r"\b(?:пр-?т\.?|проспект)", "", street)
    street = re.sub(r"\b(?:пер(?:еулок)?\.?|переулок)", "", street)
    return normalized_text(street), house


@dataclass(frozen=True)
class LocationResolution:
    district: District | None
    microdistrict: Microdistrict | None
    source: str


def resolve_location(address: str | None, district_name: str | None,
                     microdistrict_name: str | None) -> LocationResolution:
    """Use explicit parser values first, then a house-range street rule."""
    districts = list(District.objects.all())
    district = next((item for item in districts if _matches(district_name, item.name, item.aliases)), None)
    microdistricts = list(Microdistrict.objects.select_related("district").all())
    candidates = [item for item in microdistricts if _matches(microdistrict_name, item.name, item.aliases)]
    microdistrict = next((item for item in candidates if not district or item.district_id == district.id), None)
    if microdistrict:
        return LocationResolution(microdistrict.district, microdistrict, "parser")
    if district:
        return LocationResolution(district, None, "parser")

    street, house = address_street_and_house(address)
    if not street:
        return LocationResolution(None, None, "unknown")
    for rule in StreetAssignment.objects.select_related("district", "microdistrict").all():
        if address_street_and_house(rule.street)[0] != street:
            continue
        if house is not None:
            if rule.house_from is not None and house < rule.house_from:
                continue
            if rule.house_to is not None and house > rule.house_to:
                continue
            if rule.parity == StreetAssignment.Parity.ODD and house % 2 == 0:
                continue
            if rule.parity == StreetAssignment.Parity.EVEN and house % 2:
                continue
        return LocationResolution(rule.district, rule.microdistrict, "street")
    return LocationResolution(None, None, "unknown")
