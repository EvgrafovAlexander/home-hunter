from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0019_scoring_preference"),
    ]

    operations = [
        migrations.AddField(
            model_name="listingsearchquery",
            name="missed_full_scans",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
