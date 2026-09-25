#!/usr/bin/env python3
"""Extract Ufa microdistrict polygons embedded in bezposrednikov.ru HTML."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


DEFAULT_URL = "https://ufa.bezposrednikov.ru/sdam/raions"
DEFAULT_OUTPUT = Path("data/ufa_microdistrict_polygons.json")


def fetch_html(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "home-hunter/1.0 (microdistrict polygon refresh)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit public source URL
        return response.read()


def extract_polygons(html: bytes, source_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for node in soup.select('input.raion-checkbox[data-geomtype="polygon"]'):
        raw_geometry = node.get("value")
        if not raw_geometry:
            continue
        geometry = json.loads(raw_geometry)
        items.append(
            {
                "id": int(node["data-id"]),
                "title": node.get("data-title", "").strip(),
                "url": node.get("data-url", "").strip(),
                "geometry_type": "Polygon",
                # Yandex Maps 2.1 page data uses [latitude, longitude].
                "coordinate_order": "latitude,longitude",
                "coordinates": geometry,
            }
        )
    items.sort(key=lambda item: (item["title"].casefold(), item["id"]))
    return {
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "html_sha256": hashlib.sha256(html).hexdigest(),
        "count": len(items),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    html = fetch_html(args.url)
    payload = extract_polygons(html, args.url)
    if payload["count"] != 47:
        raise SystemExit(f"Expected 47 polygons, extracted {payload['count']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {payload['count']} polygons to {args.output}")


if __name__ == "__main__":
    main()
