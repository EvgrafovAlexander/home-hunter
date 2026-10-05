from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("listings", "0045_merge_inors_one_into_inors")]

    operations = [
        migrations.CreateModel(
            name="TelegramListingAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.DecimalField(decimal_places=1, max_digits=4)),
                ("is_deal", models.BooleanField(default=False)),
                ("reasons", models.JSONField(default=list)),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("listing", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_alert", to="listings.listing")),
            ],
        ),
    ]
