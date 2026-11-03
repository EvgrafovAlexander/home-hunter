from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0065_avitospfacookie_mobile")]

    operations = [
        # У старых незавершённых циклов неизвестно качество предыдущих пакетов.
        migrations.AddField(
            model_name="cianfullscancheckpoint",
            name="safe_for_deactivation",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="cianfullscancheckpoint",
            name="safe_for_deactivation",
            field=models.BooleanField(default=True),
        ),
    ]
