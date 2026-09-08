from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0012_hide_zaton_listings")]

    operations = [
        migrations.CreateModel(
            name="DealAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("discount_percent", models.IntegerField()),
                ("market_reference", models.CharField(max_length=100)),
                ("sample_size", models.PositiveIntegerField()),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("listing", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="deal_alert", to="listings.listing")),
            ],
        ),
    ]
