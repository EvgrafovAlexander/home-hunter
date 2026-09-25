"""Resolve parsed locations against the editable Ufa location directory."""

from dataclasses import dataclass
import re

from django.db.models import Q

from listings.models import District, Listing, Microdistrict, StreetAssignment
from listings.services.enrichment import location_is_visible


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
        number = re.match(r"^(\d+)(?:[\s/А-Яа-яA-Za-z-].*)?$", part)
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
    excluded: bool = False


def _matching_street_rule(address: str | None):
    street, house = address_street_and_house(address)
    if not street:
        return None
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
        return rule
    return None


def location_is_excluded(address: str | None) -> bool:
    rule = _matching_street_rule(address)
    return bool(rule and rule.is_excluded)


def location_is_visible_with_rules(
    address: str | None,
    district: str | None,
    microdistrict: str | None,
) -> bool:
    """Return base visibility while always enforcing street exclusions.

    This is deliberately separate from ``enrichment.location_is_visible``:
    the latter only knows about the static district/microdistrict denylist and
    cannot enforce editable street rules (including manual location overrides).
    """
    return location_is_visible(address, district, microdistrict) and not location_is_excluded(address)


def apply_location_rules():
    """Reapply current directory rules to already collected listings."""
    updated = 0
    unresolved = 0
    for listing in Listing.objects.all().order_by("pk").iterator(chunk_size=200):
        resolution = resolve_location(listing.address, listing.district, listing.microdistrict)
        if not resolution.district:
            unresolved += 1
            continue
        values = {
            "district_ref": resolution.district,
            "microdistrict_ref": resolution.microdistrict,
            "district": resolution.district.name,
            "microdistrict": resolution.microdistrict.name if resolution.microdistrict else listing.microdistrict,
            "is_visible": location_is_visible_with_rules(
                listing.address, resolution.district.name,
                resolution.microdistrict.name if resolution.microdistrict else listing.microdistrict,
            ),
        }
        changed = [field for field, value in values.items() if getattr(listing, field) != value]
        if changed:
            for field in changed:
                setattr(listing, field, values[field])
            listing.save(update_fields=changed)
            updated += 1
    return updated, unresolved


def apply_location_rules_for_assignments(*assignments):
    """Reapply rules only to listings that can match the changed streets."""
    assignments = [item for item in assignments if item is not None]
    if not assignments:
        return 0, 0
    tokens = set()
    for assignment in assignments:
        street, _ = address_street_and_house(assignment.street)
        tokens.update(token for token in street.split() if len(token) > 2)
    if not tokens:
        return 0, 0
    query = Q()
    for token in tokens:
        query |= Q(address__icontains=token)
    updated = unresolved = 0
    for listing in Listing.objects.filter(query).order_by("pk"):
        resolution = resolve_location(listing.address, listing.district, listing.microdistrict)
        if not resolution.district:
            unresolved += 1
            continue
        values = {
            "district_ref": resolution.district,
            "microdistrict_ref": resolution.microdistrict,
            "district": resolution.district.name,
            "microdistrict": resolution.microdistrict.name if resolution.microdistrict else listing.microdistrict,
            "is_visible": location_is_visible_with_rules(
                listing.address, resolution.district.name,
                resolution.microdistrict.name if resolution.microdistrict else listing.microdistrict,
            ),
        }
        changed = [field for field, value in values.items() if getattr(listing, field) != value]
        if changed:
            for field in changed:
                setattr(listing, field, values[field])
            listing.save(update_fields=changed)
            updated += 1
    return updated, unresolved


def resolve_location(address: str | None, district_name: str | None,
                     microdistrict_name: str | None) -> LocationResolution:
    """Use explicit parser values first, then a house-range street rule."""
    street_rule = _matching_street_rule(address)
    if street_rule:
        return LocationResolution(street_rule.district, street_rule.microdistrict, "street", street_rule.is_excluded)
    districts = list(District.objects.all())
    district = next((item for item in districts if _matches(district_name, item.name, item.aliases)), None)
    microdistricts = list(Microdistrict.objects.select_related("district").all())
    candidates = [item for item in microdistricts if _matches(microdistrict_name, item.name, item.aliases)]
    microdistrict = next((item for item in candidates if not district or item.district_id == district.id or
                          item.district_links.filter(district_id=district.id).exists()), None)
    if microdistrict:
        return LocationResolution(district or microdistrict.district, microdistrict, "parser")
    if district:
        return LocationResolution(district, None, "parser")

    return LocationResolution(None, None, "unknown")
