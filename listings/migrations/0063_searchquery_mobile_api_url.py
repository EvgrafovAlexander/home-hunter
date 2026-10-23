from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0062_persist_avito_default_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchquery",
            name="mobile_api_url",
            field=models.TextField(
                blank=True,
                default="",
                help_text="URL выдачи Mobile API Avito для того же набора фильтров.",
            ),
        ),
    ]
