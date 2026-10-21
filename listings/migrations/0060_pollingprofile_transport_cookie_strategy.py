from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0059_telegram_listing_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="pollingprofile",
            name="transport",
            field=models.CharField(
                choices=[("browser_html", "Chromium / HTML"), ("mobile_api", "Mobile API")],
                default="browser_html",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pollingprofile",
            name="cookie_strategy",
            field=models.CharField(
                choices=[
                    ("browser_profile", "Профиль Chromium"),
                    ("own_cookies", "Собственные cookies"),
                    ("spfa", "SPFA"),
                ],
                default="browser_profile",
                max_length=20,
            ),
        ),
    ]
