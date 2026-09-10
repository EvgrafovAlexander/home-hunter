import re

from django.core.management.base import BaseCommand

from listings.models import Listing


TECHNICAL_TITLE = re.compile(r"^Квартира \d+$")


class Command(BaseCommand):
    help = "Restore CIAN titles that were previously replaced by a detail-page technical fallback."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the restored titles; default is a dry run.")

    def handle(self, *args, **options):
        repaired = 0
        for listing in Listing.objects.filter(source="cian"):
            if not TECHNICAL_TITLE.fullmatch(listing.title.strip()):
                continue
            replacement = next((
                snapshot.data.get("title", "").strip()
                for snapshot in listing.snapshots.order_by("-observed_at", "-id")
                if snapshot.data.get("title", "").strip()
                and not TECHNICAL_TITLE.fullmatch(snapshot.data["title"].strip())
            ), "")
            if not replacement:
                self.stdout.write(f"{listing.external_id}: no historical title")
                continue
            repaired += 1
            self.stdout.write(f"{listing.external_id}: {replacement}")
            if options["apply"]:
                listing.title = replacement[:1000]
                listing.save(update_fields=("title", "updated_at"))
        self.stdout.write(self.style.SUCCESS(
            f"{'Restored' if options['apply'] else 'Would restore'} {repaired} CIAN title(s)."
        ))
