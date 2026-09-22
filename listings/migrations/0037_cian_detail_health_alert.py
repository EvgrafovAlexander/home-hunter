from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0034_dedupe_street_assignments")]
    operations = [migrations.CreateModel(
        name="CianDetailHealthAlert",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("state", models.CharField(default="unknown", max_length=20)),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ],
    )]
