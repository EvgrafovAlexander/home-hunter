from django.db import migrations


def hide_kalininsky_listings(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(district__iexact="Калининский").update(is_visible=False)


class Migration(migrations.Migration):
    dependencies = [("listings", "0006_listing_coordinates")]

    operations = [migrations.RunPython(hide_kalininsky_listings, migrations.RunPython.noop)]
