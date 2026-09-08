from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0007_hide_kalininsky_listings")]

    operations = [
        migrations.AlterField(
            model_name="listing",
            name="is_visible",
            field=models.BooleanField(db_default=True, db_index=True, default=True),
        ),
    ]
