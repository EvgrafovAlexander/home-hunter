import json
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class GeocodingError(RuntimeError):
    pass


@dataclass(frozen=True)
class Coordinates:
    latitude: Decimal
    longitude: Decimal


def geocode_ufa_address(address: str) -> Coordinates | None:
    """Resolve a normalized address with the public Nominatim API."""
    if not settings.NOMINATIM_USER_AGENT:
        raise GeocodingError("Set NOMINATIM_USER_AGENT before using the public geocoder")
    query = urlencode({"q": f"Уфа, Республика Башкортостан, {address}", "format": "jsonv2", "limit": 1})
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{query}",
        headers={"User-Agent": settings.NOMINATIM_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed HTTPS endpoint
            results = json.load(response)
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise GeocodingError(str(exc)) from exc
    if not results:
        return None
    result = results[0]
    try:
        return Coordinates(Decimal(result["lat"]), Decimal(result["lon"]))
    except (KeyError, ArithmeticError) as exc:
        raise GeocodingError("Nominatim returned invalid coordinates") from exc
