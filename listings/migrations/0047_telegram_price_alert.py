from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("listings", "0046_telegram_listing_alert")]

    operations = [
        migrations.CreateModel(
            name="TelegramPriceAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("old_price", models.BigIntegerField()),
                ("new_price", models.BigIntegerField()),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("listing", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_price_alerts", to="listings.listing")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("listing", "old_price", "new_price"), name="unique_telegram_price_alert")]},
        ),
    ]
