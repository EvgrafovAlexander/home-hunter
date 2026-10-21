from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("listings", "0058_polling_diagnostic_job")]

    operations = [
        migrations.AddField(
            model_name="telegramlistingalert",
            name="delivery_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="delivery_status",
            field=models.CharField(
                choices=[
                    ("awaiting_geocode", "Ожидает геокодирование"),
                    ("pending", "Ожидает отправки"),
                    ("sending", "Отправляется"),
                    ("sent", "Отправлено"),
                    ("failed", "Ошибка отправки"),
                    ("skipped", "Пропущено фильтром"),
                ],
                db_index=True,
                default="sent",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="filter_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="filter_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="last_delivery_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="telegramlistingalert",
            name="next_attempt_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="telegramlistingalert",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
