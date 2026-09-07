from django.db import migrations, models


DOMCLICK_UFA_URL = (
    "https://ufa.domclick.ru/search?deal_type=sale&category=living&offer_type=flat"
    "&aids=50560260&aids=50561813&aids=50683847&aids=50570094&aids=50561882"
    "&from=topline2020&sort=updated&sort_dir=desc&sale_price__gte=5000000"
    "&sale_price__lte=12000000&rooms=2&rooms=3&area__gte=55&floor_not_first=1"
    "&floor_not_last=1&floors__gte=9&offset=0"
)


def add_configured_search(apps, schema_editor):
    SearchQuery = apps.get_model("listings", "SearchQuery")
    if not SearchQuery.objects.filter(source="domclick", url=DOMCLICK_UFA_URL).exists():
        SearchQuery.objects.create(name="Домклик Уфа: 2–3 комнаты", source="domclick", url=DOMCLICK_UFA_URL)


class Migration(migrations.Migration):
    dependencies = [("listings", "0003_cianfullscancheckpoint")]

    operations = [
        migrations.AlterField(
            model_name="searchquery",
            name="source",
            field=models.CharField(choices=[("avito", "Avito"), ("cian", "CIAN"), ("domclick", "Домклик")], max_length=20),
        ),
        migrations.AlterField(
            model_name="listing",
            name="source",
            field=models.CharField(choices=[("avito", "Avito"), ("cian", "CIAN"), ("domclick", "Домклик")], max_length=20),
        ),
        migrations.AlterField(
            model_name="scan",
            name="source",
            field=models.CharField(choices=[("avito", "Avito"), ("cian", "CIAN"), ("domclick", "Домклик")], max_length=20),
        ),
        migrations.RunPython(add_configured_search, migrations.RunPython.noop),
    ]
