import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from listings.models import Listing
from listings.services.geocoding import GeocodingError, geocode_ufa_address


class Command(BaseCommand):
    help = "Geocode visible listing addresses via public Nominatim at no more than one request per second."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--retry-failed", action="store_true")

    def handle(self, *args, **options):
        if options["limit"] <= 0:
            raise CommandError("--limit must be positive")
        listings = Listing.objects.filter(is_visible=True, address__isnull=False).exclude(address="")
        if options["retry_failed"]:
            listings = listings.filter(latitude__isnull=True)
        else:
            listings = listings.filter(geocode_status__isnull=True)
        listings = list(listings.order_by("id")[:options["limit"]])
        for index, listing in enumerate(listings):
            if index:
                time.sleep(1)
            try:
                coordinates = geocode_ufa_address(listing.address)
            except GeocodingError as exc:
                listing.geocode_status, listing.geocoded_at = "error", timezone.now()
                listing.save(update_fields=["geocode_status", "geocoded_at"])
                self.stderr.write(f"{listing.pk}: error: {exc}")
                continue
            listing.geocoded_at = timezone.now()
            if coordinates:
                listing.latitude, listing.longitude = coordinates.latitude, coordinates.longitude
                listing.geocode_status = "success"
            else:
                listing.geocode_status = "not_found"
            listing.save(update_fields=["latitude", "longitude", "geocode_status", "geocoded_at"])
            self.stdout.write(f"{listing.pk}: {listing.geocode_status}")
        self.stdout.write(f"Processed {len(listings)} listing(s)")
