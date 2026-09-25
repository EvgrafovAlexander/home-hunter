import hashlib
import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from listings.models import Microdistrict, MicrodistrictBoundary
from listings.services.microdistrict_polygons import find_microdistrict_ref


class Command(BaseCommand):
    help = "Import source microdistrict polygons and link them to the directory."

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=Path("data/ufa_microdistrict_polygons.json"))
        parser.add_argument("--source", default="bezposrednikov")

    @transaction.atomic
    def handle(self, *args, **options):
        payload = json.loads(options["file"].read_text(encoding="utf-8"))
        directory = list(Microdistrict.objects.select_related("district"))
        retrieved_at = payload.get("retrieved_at")
        retrieved_at = datetime.fromisoformat(retrieved_at) if retrieved_at else None
        created = updated = linked = 0
        for item in payload.get("items", []):
            coordinates = item.get("coordinates") or []
            points = [point for ring in coordinates for point in ring]
            bbox = []
            if points:
                bbox = [min(p[0] for p in points), min(p[1] for p in points),
                        max(p[0] for p in points), max(p[1] for p in points)]
            geometry = {"type": item.get("geometry_type", "Polygon"), "coordinates": coordinates}
            digest = hashlib.sha256(json.dumps(geometry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            ref = find_microdistrict_ref(item.get("title", ""), directory)
            obj, was_created = MicrodistrictBoundary.objects.update_or_create(
                source=options["source"], source_polygon_id=item["id"],
                defaults={
                    "title": item.get("title", ""), "geometry": geometry, "bbox": bbox,
                    "geometry_hash": digest, "retrieved_at": retrieved_at,
                    "microdistrict": ref, "is_active": True,
                },
            )
            created += was_created
            updated += not was_created
            linked += bool(ref)
        self.stdout.write(f"Imported: {created + updated}; created: {created}; updated: {updated}; linked: {linked}.")
