from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0037_cian_detail_health_alert")]
    operations = [
        migrations.AddField(
            model_name="listing",
            name="microdistrict_source",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="microdistrict_confidence",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=4, null=True),
        ),
        migrations.AddField(
            model_name="listing",
            name="microdistrict_polygon_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
