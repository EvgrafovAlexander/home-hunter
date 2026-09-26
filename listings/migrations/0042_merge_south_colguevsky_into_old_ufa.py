from django.db import migrations


def merge_microdistricts(apps, schema_editor):
    Microdistrict = apps.get_model("listings", "Microdistrict")
    Listing = apps.get_model("listings", "Listing")
    Boundary = apps.get_model("listings", "MicrodistrictBoundary")
    Link = apps.get_model("listings", "MicrodistrictDistrict")

    target = Microdistrict.objects.filter(name="Старая Уфа").first()
    if not target:
        return
    source_ids = list(Microdistrict.objects.filter(name__in=["Южный", "Колгуевский"]).values_list("id", flat=True))
    if not source_ids:
        target.aliases = list(dict.fromkeys([*(target.aliases or []), "Южный", "Южный м-н", "Колгуевский", "Колгуевский мкр"]))
        target.save(update_fields=["aliases"])
        return

    Listing.objects.filter(microdistrict_ref_id__in=source_ids).update(
        microdistrict=target.name, microdistrict_ref=target,
    )
    Listing.objects.filter(microdistrict__in=["Южный", "Южный м-н", "Колгуевский", "Колгуевский мкр"]).update(
        microdistrict=target.name, microdistrict_ref=target,
    )
    Boundary.objects.filter(microdistrict_id__in=source_ids).update(is_active=False, microdistrict=None)
    Link.objects.filter(microdistrict_id__in=source_ids).delete()
    Microdistrict.objects.filter(id__in=source_ids).delete()
    target.aliases = list(dict.fromkeys([*(target.aliases or []), "Южный", "Южный м-н", "Колгуевский", "Колгуевский мкр"]))
    target.save(update_fields=["aliases"])


class Migration(migrations.Migration):
    dependencies = [("listings", "0041_microdistrict_multiple_districts")]

    operations = [migrations.RunPython(merge_microdistricts, migrations.RunPython.noop)]
