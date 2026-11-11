# Добавляет ручные статусы личной карточки без изменения сохранённых значений.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("listings", "0073_viewingtelegrampublication_channel_message_id_and_more"),
    ]

    operations = [
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
                    ("off_market", "Снята с продажи"),
                    ("archived", "Архив"),
                ],
                default="thinking",
                max_length=20,
                verbose_name="Статус карточки",
            ),
        ),
    ]
