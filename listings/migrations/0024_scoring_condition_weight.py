from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0023_listing_built_year")]
    operations = [migrations.AddField(
        model_name="scoringpreference", name="condition_weight",
        field=models.PositiveSmallIntegerField(default=25),
    )]
