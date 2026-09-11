from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0029_ciandetailpayload")]

    operations = [
        migrations.AddField(model_name="listing", name="kitchen_area", field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
        migrations.AddField(model_name="listing", name="living_area", field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
        migrations.AddField(model_name="listing", name="ceiling_height", field=models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True)),
        migrations.AddField(model_name="listing", name="bathrooms_combined", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="bathrooms_separate", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="balconies_count", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="loggias_count", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="repair_type", field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name="listing", name="windows_view_type", field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name="listing", name="has_furniture", field=models.BooleanField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="passenger_lifts_count", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="cargo_lifts_count", field=models.PositiveSmallIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="has_ramp", field=models.BooleanField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="building_material_type", field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name="listing", name="parking_type", field=models.CharField(blank=True, max_length=100, null=True)),
        migrations.AddField(model_name="listing", name="has_garbage_chute", field=models.BooleanField(blank=True, null=True)),
    ]
