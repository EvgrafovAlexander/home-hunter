from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("listings", "0024_scoring_condition_weight")]
    operations = [migrations.CreateModel(
        name="CianDetailPollState",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("status", models.CharField(choices=[("unknown", "Не проверено"), ("published", "Активно"), ("unavailable", "Недоступно")], default="unknown", max_length=20)),
            ("last_checked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
            ("last_available_at", models.DateTimeField(blank=True, null=True)),
            ("first_unavailable_at", models.DateTimeField(blank=True, null=True)),
            ("last_error", models.TextField(blank=True)),
            ("detail_data", models.JSONField(blank=True, default=dict)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("listing", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cian_detail_state", to="listings.listing")),
        ],
    )]
