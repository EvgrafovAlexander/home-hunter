from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0027_listing_publication_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="listing",
            name="publication_status",
            field=models.CharField(
                choices=[("published", "Опубликовано"), ("unavailable", "Снято / недоступно")],
                db_default="published", db_index=True, default="published", max_length=20,
            ),
        ),
    ]
