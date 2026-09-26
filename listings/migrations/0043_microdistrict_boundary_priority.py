from django.db import migrations, models


def prioritize_manual_detail_boundaries(apps, schema_editor):
    Boundary = apps.get_model("listings", "MicrodistrictBoundary")
    Boundary.objects.filter(source="manual").exclude(
        microdistrict__name__in=["Центр", "Старая Уфа"],
    ).update(priority=100)


class Migration(migrations.Migration):
    dependencies = [("listings", "0042_merge_south_colguevsky_into_old_ufa")]

    operations = [
        migrations.AddField(
            model_name="microdistrictboundary",
            name="priority",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Higher priority wins when active polygons overlap",
            ),
        ),
        migrations.RunPython(prioritize_manual_detail_boundaries, migrations.RunPython.noop),
    ]
