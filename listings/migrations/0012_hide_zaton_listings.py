from django.db import migrations


def hide_zaton_listings(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(microdistrict__iexact="Затон").update(is_visible=False)


class Migration(migrations.Migration):
    dependencies = [("listings", "0011_source_polling_control_mode")]

    operations = [migrations.RunPython(hide_zaton_listings, migrations.RunPython.noop)]
