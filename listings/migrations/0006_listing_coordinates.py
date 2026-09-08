from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0005_listing_address_fields")]

    operations = [
        migrations.AddField(model_name="listing", name="latitude",
                            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
        migrations.AddField(model_name="listing", name="longitude",
                            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
        migrations.AddField(model_name="listing", name="geocode_status",
                            field=models.CharField(blank=True, max_length=20, null=True)),
        migrations.AddField(model_name="listing", name="geocoded_at",
                            field=models.DateTimeField(blank=True, null=True)),
    ]
