from django.db import migrations


def merge_inors_one(apps, schema_editor):
    Microdistrict = apps.get_model("listings", "Microdistrict")
    Listing = apps.get_model("listings", "Listing")
    Boundary = apps.get_model("listings", "MicrodistrictBoundary")
    Link = apps.get_model("listings", "MicrodistrictDistrict")

    target = Microdistrict.objects.filter(name="Инорс").first()
    source = Microdistrict.objects.filter(name="Инорс-1").first()
    if not target:
        return
    if source:
        Listing.objects.filter(microdistrict_ref_id=source.pk).update(
            microdistrict=target.name, microdistrict_ref=target,
        )
        Listing.objects.filter(microdistrict="Инорс-1").update(
            microdistrict=target.name, microdistrict_ref=target,
        )
        Boundary.objects.filter(microdistrict_id=source.pk).update(is_active=False, microdistrict=None)
        Link.objects.filter(microdistrict_id=source.pk).delete()
        source.delete()
    target.aliases = list(dict.fromkeys([*(target.aliases or []), "Инорс-1"]))
    target.save(update_fields=["aliases"])


class Migration(migrations.Migration):
    dependencies = [("listings", "0044_normalize_microdistrict_names")]

    operations = [migrations.RunPython(merge_inors_one, migrations.RunPython.noop)]
