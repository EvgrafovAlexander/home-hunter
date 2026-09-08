from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0008_listing_is_visible_db_default")]

    operations = [
        migrations.CreateModel(
            name="SourceHealthAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(choices=[("avito", "Avito"), ("cian", "CIAN"), ("domclick", "Домклик")], max_length=20, unique=True)),
                ("state", models.CharField(max_length=30)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
