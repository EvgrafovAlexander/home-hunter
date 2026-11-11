# Разделяет этап работы с квартирой и доступность объявления на рынке.

from django.db import migrations, models


def move_off_market_status(apps, schema_editor):
    """Сохраняет снятие с продажи в новом поле, не оставляя устаревший статус."""
    apartment = apps.get_model("listings", "ViewingApartment")
    apartment.objects.filter(decision="off_market").update(
        decision="thinking",
        availability_status="off_market",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0074_alter_viewingapartment_decision"),
    ]

    operations = [
        migrations.AddField(
            model_name="viewingapartment",
            name="availability_status",
            field=models.CharField(
                choices=[
                    ("unknown", "Не проверено"),
                    ("active", "В продаже"),
                    ("off_market", "Снята с продажи"),
                ],
                default="unknown",
                max_length=20,
                verbose_name="Статус объявления",
            ),
        ),
        migrations.RunPython(move_off_market_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="viewingapartment",
            name="decision",
            field=models.CharField(
                choices=[
                    ("thinking", "Не просмотрена"),
                    ("contact", "Нужно связаться"),
                    ("viewing_planned", "Просмотр назначен"),
                    ("viewed", "Просмотрена"),
                    ("suitable", "Подходит"),
                    ("rejected", "Отказ"),
                    ("archived", "Архив"),
                ],
                default="thinking",
                max_length=20,
                verbose_name="Статус карточки",
            ),
        ),
    ]
