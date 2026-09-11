from django.core.management.base import BaseCommand

from collectors.cian.parser import detail_attributes
from listings.models import CianDetailPayload, Listing


class Command(BaseCommand):
    help = "Fill CIAN detail attributes from already saved complete payloads."

    def handle(self, *args, **options):
        updated = 0
        for detail_payload in CianDetailPayload.objects.select_related("listing").iterator():
            values = {
                field: value
                for field, value in detail_attributes(detail_payload.payload).items()
                if value is not None
            }
            if not values:
                continue
            listing = detail_payload.listing
            changed = {
                field: value for field, value in values.items()
                if getattr(listing, field) != value
            }
            if changed:
                Listing.objects.filter(pk=listing.pk).update(**changed)
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} CIAN listings."))
