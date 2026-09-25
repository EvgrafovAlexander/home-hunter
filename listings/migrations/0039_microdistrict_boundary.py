from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("listings", "0038_listing_microdistrict_metadata")]

    operations = [
        migrations.CreateModel(
            name="MicrodistrictBoundary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(max_length=50)),
                ("source_polygon_id", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=255)),
                ("geometry", models.JSONField()),
                ("bbox", models.JSONField(blank=True, default=list)),
                ("geometry_hash", models.CharField(max_length=64)),
                ("confidence", models.DecimalField(decimal_places=3, default=0.98, max_digits=4)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("retrieved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("microdistrict", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="boundaries", to="listings.microdistrict")),
            ],
            options={"ordering": ("source", "title", "source_polygon_id")},
        ),
        migrations.AddConstraint(
            model_name="microdistrictboundary",
            constraint=models.UniqueConstraint(fields=("source", "source_polygon_id"), name="unique_boundary_source_polygon"),
        ),
    ]
