from django.core.management.base import BaseCommand

from listings.models import Listing
from listings.services.enrichment import location_is_visible
from listings.services.location_directory import resolve_location


class Command(BaseCommand):
    help = "Match stored listings to the district/microdistrict directory."

    def add_arguments(self, parser):
        parser.add_argument("--only-missing", action="store_true", help="Skip listings already linked to a district.")

    def handle(self, *args, **options):
        listings = Listing.objects.all().order_by("pk")
        if options["only_missing"]:
            listings = listings.filter(district_ref__isnull=True)
        updated = unresolved = 0
        for listing in listings.iterator(chunk_size=200):
            resolution = resolve_location(listing.address, listing.district, listing.microdistrict)
            if not resolution.district:
                unresolved += 1
                continue
            values = {
                "district_ref": resolution.district,
                "microdistrict_ref": resolution.microdistrict,
                "location_source": "manual" if listing.location_source == "manual" else resolution.source,
                "district": resolution.district.name,
                "microdistrict": resolution.microdistrict.name if resolution.microdistrict else listing.microdistrict,
            }
            values["is_visible"] = location_is_visible(listing.address, values["district"], values["microdistrict"])
            changed = [field for field, value in values.items() if getattr(listing, field) != value]
            if changed:
                for field in changed:
                    setattr(listing, field, values[field])
                listing.save(update_fields=changed)
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Updated: {updated}; unresolved: {unresolved}."))
