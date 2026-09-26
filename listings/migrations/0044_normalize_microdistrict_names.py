from django.db import migrations


def normalize_names(apps, schema_editor):
    District = apps.get_model("listings", "District")
    Microdistrict = apps.get_model("listings", "Microdistrict")
    Listing = apps.get_model("listings", "Listing")

    target_names = {
        "Дёма": "Дёма",
        "Дема": "Дёма",
        "Молодежный": "Молодёжный",
        "Кузнецовский затон": "Кузнецовский Затон",
        "Кузнецовский Затон": "Кузнецовский Затон",
    }
    targets = {
        name: Microdistrict.objects.filter(name=canonical).first()
        for name, canonical in target_names.items()
    }

    oktyabrsky = District.objects.filter(name="Октябрьский").first()
    if oktyabrsky:
        october, _ = Microdistrict.objects.get_or_create(
            district=oktyabrsky,
            name="Проспект Октября",
            defaults={"aliases": ["пр-т Октября", "проспект Октября"]},
        )
        october.aliases = list(dict.fromkeys([*(october.aliases or []), "пр-т Октября", "проспект Октября"]))
        october.save(update_fields=["aliases"])
        targets.update({"проспект Октября": october, "Пр-кт Октября": october})

    for source_name, target in targets.items():
        if not target:
            continue
        Listing.objects.filter(microdistrict=source_name).update(
            microdistrict=target.name,
            microdistrict_ref=target,
        )


class Migration(migrations.Migration):
    dependencies = [("listings", "0043_microdistrict_boundary_priority")]

    operations = [migrations.RunPython(normalize_names, migrations.RunPython.noop)]
