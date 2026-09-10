from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0025_ciandetailpollstate"),
    ]

    operations = [
        migrations.CreateModel(
            name="CianDetailPollProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(default="cian", max_length=20, unique=True)),
                ("cycle_started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("cycle_total", models.PositiveIntegerField(default=0)),
                ("completed_listing_ids", models.JSONField(blank=True, default=list)),
                ("current_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("current_listing", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="listings.listing")),
            ],
        ),
    ]
