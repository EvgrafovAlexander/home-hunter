from dataclasses import dataclass, field
from collectors.base import NormalizedListing


@dataclass
class ParsedPage:
    listings: list[NormalizedListing] = field(default_factory=list)
    card_count: int = 0
    errors: int = 0
