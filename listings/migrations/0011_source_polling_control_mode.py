from django.db import migrations, models


def split_source_controls(apps, schema_editor):
    Control = apps.get_model("listings", "SourcePollingControl")
    existing = {control.source: control.enabled for control in Control.objects.all()}
    for source, enabled in existing.items():
        Control.objects.filter(source=source).update(mode="fast")
        if source == "avito":
            Control.objects.get_or_create(source=source, mode="full", defaults={"enabled": False})
        elif source == "cian":
            Control.objects.get_or_create(source=source, mode="full", defaults={"enabled": enabled})


class Migration(migrations.Migration):

    dependencies = [("listings", "0010_source_polling_control")]

    operations = [
        migrations.AlterField(
            model_name="sourcepollingcontrol",
            name="source",
            field=models.CharField(choices=[("avito", "Avito"), ("cian", "CIAN"), ("domclick", "Домклик")], max_length=20),
        ),
        migrations.AddField(
            model_name="sourcepollingcontrol",
            name="mode",
            field=models.CharField(choices=[("fast", "Fast"), ("full", "Full")], default="fast", max_length=10),
        ),
        migrations.RunPython(split_source_controls, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="sourcepollingcontrol",
            constraint=models.UniqueConstraint(fields=("source", "mode"), name="unique_source_polling_mode"),
        ),
    ]
