from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0021_cianfullscancheckpoint_cycle_state")]
    operations = [
        migrations.AddField(model_name="scoringpreference", name="location_weight", field=models.PositiveSmallIntegerField(default=35)),
        migrations.AddField(model_name="scoringpreference", name="use_ufa_target_zones", field=models.BooleanField(default=True)),
    ]
