import re
from decimal import Decimal, ROUND_HALF_UP


DISTRICT_PATTERN = re.compile(r"(?:^|[\s,·])р-н\s+([^·,]+?)\s*$", re.IGNORECASE)


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
