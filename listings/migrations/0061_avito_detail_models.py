from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("listings", "0060_pollingprofile_transport_cookie_strategy")]
    operations = [
        migrations.CreateModel(
            name="AvitoDetailPollState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("unknown", "Не проверено"), ("published", "Активно"), ("unavailable", "Недоступно")], default="unknown", max_length=20)),
                ("last_checked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_available_at", models.DateTimeField(blank=True, null=True)),
                ("first_unavailable_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("detail_data", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("listing", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="avito_detail_state", to="listings.listing")),
            ],
        ),
        migrations.CreateModel(
            name="AvitoDetailPayload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()), ("sha256", models.CharField(max_length=64)),
                ("first_received_at", models.DateTimeField(default=timezone.now)),
                ("last_received_at", models.DateTimeField(default=timezone.now)),
                ("listing", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="avito_detail_payload", to="listings.listing")),
            ],
        ),
        migrations.CreateModel(
            name="AvitoDetailPollProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(default="avito", max_length=20, unique=True)),
                ("cycle_started_at", models.DateTimeField(default=timezone.now)),
                ("cycle_total", models.PositiveIntegerField(default=0)),
                ("completed_listing_ids", models.JSONField(blank=True, default=list)),
                ("current_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("current_listing", models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="+", to="listings.listing")),
            ],
        ),
    ]
