from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from listings.models import SearchQuery


@dataclass
class NormalizedListing:
    source: str
    external_id: str
    url: str
    title: str
    price: int | None = None
    price_per_sqm: int | None = None
    rooms: int | None = None
    area: Decimal | None = None
    floor: int | None = None
    floors_total: int | None = None
    address: str | None = None
    district: str | None = None
    description: str | None = None
    published_text: str | None = None
    image_url: str | None = None
    raw_data: dict = field(default_factory=dict)


@dataclass
class CollectionResult:
    listings: list[NormalizedListing] = field(default_factory=list)
    pages_scanned: int = 0
    items_seen: int = 0
    complete: bool = False
    stop_reason: str = ""
    next_page: int | None = None
    expected_total: int | None = None
    total_changes: int = 0


class CollectionError(RuntimeError):
    def __init__(self, message: str, result: CollectionResult):
        super().__init__(message)
        self.result = result


class BaseCollector(ABC):
    @abstractmethod
    async def collect(self, search: "SearchQuery", *, mode: str) -> CollectionResult:
        ...
