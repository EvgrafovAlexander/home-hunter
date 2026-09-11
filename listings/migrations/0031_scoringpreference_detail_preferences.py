from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0030_listing_cian_detail_attributes")]

    operations = [
        migrations.AddField(
            model_name="scoringpreference",
            name="min_kitchen_area",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name="scoringpreference",
            name="preferred_repair_types",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="scoringpreference",
            name="prefer_furniture",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="scoringpreference",
            name="require_balcony",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="scoringpreference",
            name="require_lift",
            field=models.BooleanField(default=False),
        ),
    ]
