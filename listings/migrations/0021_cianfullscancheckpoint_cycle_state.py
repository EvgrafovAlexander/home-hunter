from django.db import migrations, models
import django.utils.timezone


def use_existing_checkpoint_timestamp(apps, schema_editor):
    Checkpoint = apps.get_model("listings", "CianFullScanCheckpoint")
    for checkpoint in Checkpoint.objects.all().only("id", "updated_at"):
        checkpoint.cycle_started_at = checkpoint.updated_at
        checkpoint.save(update_fields=("cycle_started_at",))


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0020_listingsearchquery_missed_full_scans"),
    ]

    operations = [
        migrations.AddField(
            model_name="cianfullscancheckpoint",
            name="cycle_started_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="cianfullscancheckpoint",
            name="total_changes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(use_existing_checkpoint_timestamp, migrations.RunPython.noop),
    ]
