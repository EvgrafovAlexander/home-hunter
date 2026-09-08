from django.db import migrations, models


def backfill_addresses(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    from listings.services.enrichment import parse_address

    for listing in Listing.objects.iterator():
        parsed = parse_address(listing.address, listing.district)
        Listing.objects.filter(pk=listing.pk).update(
            address=parsed.address,
            district=parsed.district,
            microdistrict=parsed.microdistrict,
            is_visible=parsed.is_visible,
        )


class Migration(migrations.Migration):
    dependencies = [("listings", "0004_add_domclick_source")]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="microdistrict",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="is_visible",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.RunPython(backfill_addresses, migrations.RunPython.noop),
    ]
