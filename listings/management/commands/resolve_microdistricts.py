import csv
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Q

from listings.models import Listing, Microdistrict, MicrodistrictBoundary
from listings.services.location_directory import location_is_visible_with_rules
from listings.services.microdistrict_polygons import find_microdistrict_ref, resolve_microdistrict


class Command(BaseCommand):
    help = "Resolve missing microdistricts from stored coordinates and Ufa polygons."

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=["avito", "cian", "domclick"])
        parser.add_argument("--only-missing", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--report-unresolved", type=Path,
            help="Write unresolved listings with coordinates to a CSV file.",
        )

    def handle(self, *args, **options):
        listings = Listing.objects.all().order_by("pk")
        if options["source"]:
            listings = listings.filter(source=options["source"])
        if options["only_missing"]:
            listings = listings.filter(Q(microdistrict__isnull=True) | Q(microdistrict=""))
        directory = list(Microdistrict.objects.select_related("district"))
        boundaries = {
            item.source_polygon_id: item
            for item in MicrodistrictBoundary.objects.filter(source="bezposrednikov", is_active=True)
        }
        matched = updated = would_update = skipped = manual = 0
        unresolved_rows = []
        for listing in listings.iterator(chunk_size=200):
            if listing.location_source == "manual" or listing.microdistrict_override:
                manual += 1
                continue
            result = resolve_microdistrict(listing.latitude, listing.longitude)
            if not result:
                skipped += 1
                if options["report_unresolved"] and listing.latitude is not None and listing.longitude is not None:
                    unresolved_rows.append({
                        "listing_id": listing.pk,
                        "external_id": listing.external_id,
                        "source": listing.source,
                        "address": listing.address or "",
                        "district": listing.district or "",
                        "latitude": listing.latitude,
                        "longitude": listing.longitude,
                    })
                continue
            matched += 1
            microdistrict_ref = find_microdistrict_ref(result.title, directory)
            canonical_name = microdistrict_ref.name if microdistrict_ref else result.title
            values = {
                "microdistrict": canonical_name,
                "microdistrict_ref": microdistrict_ref,
                "microdistrict_source": "polygon",
                "microdistrict_confidence": result.confidence,
                "microdistrict_polygon_id": result.polygon_id,
                "microdistrict_boundary": boundaries.get(result.polygon_id),
                "is_visible": location_is_visible_with_rules(
                    listing.address, listing.district,
                    canonical_name,
                ),
            }
            if microdistrict_ref:
                values["district"] = microdistrict_ref.district.name
                values["district_ref"] = microdistrict_ref.district
            changed = [field for field, value in values.items() if getattr(listing, field) != value]
            if changed:
                would_update += 1
            if changed and not options["dry_run"]:
                for field in changed:
                    setattr(listing, field, values[field])
                listing.save(update_fields=changed)
                updated += 1
        mode = "Would update" if options["dry_run"] else "Updated"
        update_count = would_update if options["dry_run"] else updated
        if options["report_unresolved"]:
            report_path = options["report_unresolved"]
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("w", newline="", encoding="utf-8") as report_file:
                writer = csv.DictWriter(report_file, fieldnames=[
                    "listing_id", "external_id", "source", "address", "district", "latitude", "longitude",
                ])
                writer.writeheader()
                writer.writerows(unresolved_rows)
            self.stdout.write(f"Unresolved report: {report_path} ({len(unresolved_rows)} row(s)).")
        self.stdout.write(
            f"Matched: {matched}; {mode}: {update_count}; unresolved: {skipped}; manual: {manual}."
        )
