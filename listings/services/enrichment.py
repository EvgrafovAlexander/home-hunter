import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


DISTRICT_PATTERN = re.compile(r"(?:^|[\s,·])р-н\s+([^·,]+?)\s*$", re.IGNORECASE)
DISTRICT_COMPONENT_PATTERN = re.compile(r"^(?:р-н|район)\s+(.+)$|^(.+?)\s+район$", re.IGNORECASE)
MICRODISTRICT_PATTERN = re.compile(r"^(?:мкр\.?|микрорайон)\s+(.+)$|^(.+?)\s+мкр\.?$", re.IGNORECASE)
IGNORED_ADDRESS_COMPONENTS = {"республика башкортостан", "башкортостан", "уфа", "г. уфа", "город уфа"}
HIDDEN_DISTRICTS = {"дёмский", "демский", "калининский"}


@dataclass(frozen=True)
class ParsedAddress:
    address: str | None
    district: str | None
    microdistrict: str | None
    is_visible: bool


def calculate_price_per_sqm(price: int | None, area: Decimal | None) -> int | None:
    """Return a rounded rouble price per square metre when both inputs are valid."""
    if price is None or price < 0 or area is None or not area.is_finite() or area <= 0:
        return None
    return int((Decimal(price) / area).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def district_from_address(address: str | None) -> str | None:
    if not address:
        return None
    match = DISTRICT_PATTERN.search(address)
    return match.group(1).strip() or None if match else None


def parse_address(address: str | None, district: str | None = None) -> ParsedAddress:
    """Normalize an Ufa address and pull its district and microdistrict into fields."""
    parsed_district = district.strip() if district and district.strip() else district_from_address(address)
    # Avito and Domclick append ratings and a district after a middle dot.  It is
    # listing-card metadata, not an address component.
    address_without_card_metadata = re.split(r"\s*·", address or "", maxsplit=1)[0]
    components = [part.strip() for part in address_without_card_metadata.split(",") if part.strip()]
    components = [part for part in components if part.casefold() not in IGNORED_ADDRESS_COMPONENTS]
    microdistrict = None
    kept = []
    for component in components:
        district_match = DISTRICT_COMPONENT_PATTERN.match(component)
        microdistrict_match = MICRODISTRICT_PATTERN.match(component)
        if district_match:
            if not parsed_district:
                parsed_district = (district_match.group(1) or district_match.group(2)).strip()
        elif microdistrict_match:
            microdistrict = (microdistrict_match.group(1) or microdistrict_match.group(2)).strip() or None
        else:
            kept.append(component)
    normalized_address = ", ".join(kept) or None
    is_visible = bool(normalized_address) and (parsed_district or "").casefold() not in HIDDEN_DISTRICTS
    return ParsedAddress(normalized_address, parsed_district, microdistrict, is_visible)
