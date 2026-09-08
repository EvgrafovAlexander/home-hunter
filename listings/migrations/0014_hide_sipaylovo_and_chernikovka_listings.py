from django.db import migrations


def hide_microdistrict_listings(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(microdistrict__in=["Сипайлово", "Черниковка"]).update(is_visible=False)


class Migration(migrations.Migration):
    dependencies = [("listings", "0013_deal_alert")]

    operations = [migrations.RunPython(hide_microdistrict_listings, migrations.RunPython.noop)]
