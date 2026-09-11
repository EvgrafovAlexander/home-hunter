from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0028_listing_publication_status_db_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="CianDetailPayload",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("sha256", models.CharField(max_length=64)),
                ("first_received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("listing", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cian_detail_payload", to="listings.listing")),
            ],
        ),
    ]
