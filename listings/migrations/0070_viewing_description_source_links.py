# Описание и ссылки необязательны; DB-default сохраняет совместимость при выкладке.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0069_viewingapartment_viewingentry_viewingphoto_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='viewingapartment',
            name='avito_url',
            field=models.URLField(blank=True, db_default="", max_length=2000, verbose_name='Ссылка на Авито'),
        ),
        migrations.AddField(
            model_name='viewingapartment',
            name='cian_url',
            field=models.URLField(blank=True, db_default="", max_length=2000, verbose_name='Ссылка на ЦИАН'),
        ),
        migrations.AddField(
            model_name='viewingapartment',
            name='description',
            field=models.TextField(blank=True, db_default="", verbose_name='Описание квартиры'),
        ),
        migrations.AddField(
            model_name='viewingapartment',
            name='domclick_url',
            field=models.URLField(blank=True, db_default="", max_length=2000, verbose_name='Ссылка на Домклик'),
        ),
    ]
