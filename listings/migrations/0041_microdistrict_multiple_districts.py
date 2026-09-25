from django.db import migrations, models
import django.db.models.deletion


def populate_links(apps, schema_editor):
    District = apps.get_model("listings", "District")
    Microdistrict = apps.get_model("listings", "Microdistrict")
    Link = apps.get_model("listings", "MicrodistrictDistrict")
    for microdistrict in Microdistrict.objects.all().iterator():
        Link.objects.get_or_create(
            microdistrict_id=microdistrict.pk,
            district_id=microdistrict.district_id,
            defaults={"is_primary": True},
        )
    soviet = District.objects.filter(name="Советский").first()
    kirov = District.objects.filter(name="Кировский").first()
    if not soviet or not kirov:
        return
    green, _ = Microdistrict.objects.get_or_create(
        district_id=soviet.pk, name="Зелёная роща",
        defaults={"aliases": ["Зеленая роща"], "sort_order": 0},
    )
    Link.objects.get_or_create(microdistrict_id=green.pk, district_id=soviet.pk,
                               defaults={"is_primary": True})
    Link.objects.get_or_create(microdistrict_id=green.pk, district_id=kirov.pk,
                               defaults={"is_primary": False})


class Migration(migrations.Migration):
    dependencies = [("listings", "0040_listing_microdistrict_boundary")]

    operations = [
        migrations.CreateModel(
            name="MicrodistrictDistrict",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                ("district", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="microdistrict_links", to="listings.district")),
                ("microdistrict", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="district_links", to="listings.microdistrict")),
            ],
        ),
        migrations.AddField(
            model_name="microdistrict",
            name="districts",
            field=models.ManyToManyField(blank=True, related_name="all_microdistricts",
                through="listings.MicrodistrictDistrict", to="listings.district"),
        ),
        migrations.AddConstraint(
            model_name="microdistrictdistrict",
            constraint=models.UniqueConstraint(fields=("microdistrict", "district"),
                name="unique_microdistrict_district_link"),
        ),
        migrations.RunPython(populate_links, migrations.RunPython.noop),
    ]
