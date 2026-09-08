from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0014_hide_sipaylovo_and_chernikovka_listings")]

    operations = [
        migrations.CreateModel(
            name="ManualDomclickJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "Ожидает ноутбук"), ("running", "Выполняется на ноутбуке"), ("success", "Готово"), ("failed", "Ошибка")], db_index=True, default="queued", max_length=20)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("scan", models.OneToOneField(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="manual_domclick_job", to="listings.scan")),
                ("search_query", models.ForeignKey(on_delete=models.deletion.PROTECT, related_name="manual_domclick_jobs", to="listings.searchquery")),
            ],
        ),
    ]
