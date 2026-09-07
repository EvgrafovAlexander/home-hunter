from decimal import Decimal, ROUND_HALF_UP
import re

from django.db import migrations
from django.db.models import Q


DISTRICT_PATTERN = re.compile(r"(?:^|[\s,·])р-н\s+([^·,]+?)\s*$", re.IGNORECASE)


def backfill_listing_enrichment(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    for listing in Listing.objects.filter(price_per_sqm__isnull=True, price__isnull=False, area__gt=0).iterator():
        listing.price_per_sqm = int((Decimal(listing.price) / listing.area).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP,
        ))
        listing.save(update_fields=["price_per_sqm"])
    for listing in Listing.objects.filter(
        Q(district__isnull=True) | Q(district=""), address__isnull=False,
    ).iterator():
        match = DISTRICT_PATTERN.search(listing.address)
        if match and match.group(1).strip():
            listing.district = match.group(1).strip()
            listing.save(update_fields=["district"])


class Migration(migrations.Migration):
    dependencies = [("listings", "0001_initial")]

    operations = [migrations.RunPython(backfill_listing_enrichment, migrations.RunPython.noop)]
