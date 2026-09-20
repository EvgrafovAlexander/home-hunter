from django.db import migrations, models


def add_zaton_rule(apps, schema_editor):
    District = apps.get_model("listings", "District")
    Microdistrict = apps.get_model("listings", "Microdistrict")
    StreetAssignment = apps.get_model("listings", "StreetAssignment")
    district = District.objects.filter(name__iexact="Ленинский").first()
    microdistrict = Microdistrict.objects.filter(district=district, name__iexact="Затон").first() if district else None
    if district and microdistrict:
        StreetAssignment.objects.get_or_create(
            street="Лётчиков", house_from=None, house_to=None, parity="any",
            defaults={
                "district": district, "microdistrict": microdistrict,
                "is_excluded": True, "note": "Неинтересная удалённая локация",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("listings", "0032_listing_reviews")]
    operations = [
        migrations.AddField(
            model_name="streetassignment",
            name="is_excluded",
            field=models.BooleanField(default=False, help_text="Исключить объявления этой улицы из выдачи и рыночной статистики"),
        ),
        migrations.RunPython(add_zaton_rule, migrations.RunPython.noop),
    ]
