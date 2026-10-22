"""Небольшой устойчивый парсер JSON детальной карточки Avito."""
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class AvitoDetail:
    status: str
    payload: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    photos: list[dict] = field(default_factory=list)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    views: int | None = None


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _decimal(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and -180 <= value <= 180 else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def parse_detail_payload(payload: dict) -> AvitoDetail:
    if not isinstance(payload, dict):
        raise ValueError("Avito detail response must be an object")
    root = payload.get("success", payload)
    if root in (None, False):
        return AvitoDetail(status="unavailable", payload=payload)
    nodes = list(_walk(root))
    coords = next((node for node in nodes if _decimal(node.get("lat")) is not None and _decimal(node.get("lon")) is not None), None)
    if coords is None:
        coords = next((node for node in nodes if _decimal(node.get("latitude")) is not None and _decimal(node.get("longitude")) is not None), None)
    latitude = _decimal((coords or {}).get("lat", (coords or {}).get("latitude")))
    longitude = _decimal((coords or {}).get("lon", (coords or {}).get("longitude")))
    photos = []
    for node in nodes:
        for key in ("images", "gallery", "photos"):
            value = node.get(key)
            if isinstance(value, list):
                for image in value:
                    if isinstance(image, str):
                        photos.append({"full_url": image})
                    elif isinstance(image, dict):
                        url = next((v for v in image.values() if isinstance(v, str) and v.startswith(("http://", "https://"))), None)
                        if url:
                            photos.append({"full_url": url})
            elif isinstance(value, dict):
                url = next((v for v in value.values() if isinstance(v, str) and v.startswith(("http://", "https://"))), None)
                if url:
                    photos.append({"full_url": url})
    unique = []
    seen = set()
    for photo in photos:
        if photo["full_url"] not in seen:
            seen.add(photo["full_url"])
            unique.append(photo)
    views = next((node.get("views") for node in nodes if isinstance(node.get("views"), int)), None)
    return AvitoDetail(status="published", payload=payload, data=root if isinstance(root, dict) else {}, photos=unique,
                       latitude=latitude, longitude=longitude, views=views)
