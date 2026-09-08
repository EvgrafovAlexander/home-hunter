from django.db import migrations, models
import django.db.models.deletion


DISTRICTS = (
    ("Дёмский", ["Демский"], 10), ("Калининский", [], 20), ("Кировский", [], 30),
    ("Ленинский", [], 40), ("Октябрьский", [], 50), ("Орджоникидзевский", [], 60),
    ("Советский", [], 70),
)
MICRODISTRICTS = (
    ("Дёмский", "Дёма", ["Дема"], 10),
    ("Калининский", "Инорс", [], 10), ("Калининский", "Инорс-1", [], 20), ("Калининский", "Шакша", [], 30),
    ("Кировский", "Колгуевский", [], 10), ("Кировский", "Радио", [], 20), ("Кировский", "Старая Уфа", [], 30), ("Кировский", "Южный", [], 40),
    ("Ленинский", "Затон", [], 10), ("Ленинский", "Центр", [], 20),
    ("Октябрьский", "Восточный", [], 10), ("Октябрьский", "Глумилино", [], 20), ("Октябрьский", "Лесопарковый", [], 30), ("Октябрьский", "Сипайлово", [], 40), ("Октябрьский", "Старый Аэропорт", [], 50),
    ("Орджоникидзевский", "Черниковка", [], 10),
    ("Советский", "Молодёжный", ["Молодежный"], 10), ("Советский", "Новиковка", [], 20), ("Советский", "Утренний", [], 30),
)


def seed_directory(apps, schema_editor):
    District = apps.get_model("listings", "District")
    Microdistrict = apps.get_model("listings", "Microdistrict")
    by_name = {}
    for name, aliases, sort_order in DISTRICTS:
        district, _ = District.objects.get_or_create(name=name, defaults={"aliases": aliases, "sort_order": sort_order})
        by_name[name] = district
    for district_name, name, aliases, sort_order in MICRODISTRICTS:
        Microdistrict.objects.get_or_create(district=by_name[district_name], name=name, defaults={"aliases": aliases, "sort_order": sort_order})


class Migration(migrations.Migration):
    dependencies = [("listings", "0016_listing_location_overrides")]

    operations = [
        migrations.CreateModel(
            name="District",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=100, unique=True)), ("aliases", models.JSONField(blank=True, default=list)), ("sort_order", models.PositiveSmallIntegerField(default=0))],
            options={"ordering": ("sort_order", "name")},
        ),
        migrations.CreateModel(
            name="Microdistrict",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=100)), ("aliases", models.JSONField(blank=True, default=list)), ("sort_order", models.PositiveSmallIntegerField(default=0)), ("district", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="microdistricts", to="listings.district"))],
            options={"ordering": ("district__sort_order", "sort_order", "name")},
        ),
        migrations.CreateModel(
            name="StreetAssignment",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("street", models.CharField(db_index=True, max_length=255)), ("house_from", models.PositiveIntegerField(blank=True, null=True)), ("house_to", models.PositiveIntegerField(blank=True, null=True)), ("parity", models.CharField(choices=[("any", "Любая"), ("odd", "Нечётные"), ("even", "Чётные")], default="any", max_length=10)), ("note", models.CharField(blank=True, max_length=255)), ("district", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="street_assignments", to="listings.district")), ("microdistrict", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="street_assignments", to="listings.microdistrict"))],
            options={"ordering": ("street", "house_from", "house_to")},
        ),
        migrations.AddField(model_name="listing", name="district_ref", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="listings", to="listings.district")),
        migrations.AddField(model_name="listing", name="location_source", field=models.CharField(default="parser", max_length=20)),
        migrations.AddField(model_name="listing", name="microdistrict_ref", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="listings", to="listings.microdistrict")),
        migrations.AddConstraint(model_name="microdistrict", constraint=models.UniqueConstraint(fields=("district", "name"), name="unique_district_microdistrict")),
        migrations.RunPython(seed_directory, migrations.RunPython.noop),
    ]
