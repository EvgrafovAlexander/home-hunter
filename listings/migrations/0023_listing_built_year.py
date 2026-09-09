from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0022_scoring_location_profile")]
    operations = [migrations.AddField(
        model_name="listing", name="built_year",
        field=models.PositiveSmallIntegerField(blank=True, db_index=True, null=True),
    )]
