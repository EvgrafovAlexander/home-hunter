from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("listings", "0039_microdistrict_boundary")]

    operations = [migrations.AddField(
        model_name="listing", name="microdistrict_boundary",
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                 related_name="listings", to="listings.microdistrictboundary"),
    )]
