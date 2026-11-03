# Дневные наблюдения рынка без изменения существующей истории объявлений.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0067_avitomanualcheck'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketDailySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField(db_index=True)),
                ('source', models.CharField(max_length=20)),
                ('captured_at', models.DateTimeField()),
                ('methodology_version', models.PositiveSmallIntegerField(default=1)),
                ('scope_hash', models.CharField(max_length=64)),
                ('quality', models.JSONField(default=dict)),
                ('listings', models.JSONField(default=list)),
            ],
            options={
                'ordering': ('day', 'source'),
                'constraints': [models.UniqueConstraint(fields=('day', 'source'), name='unique_market_day_source')],
            },
        ),
    ]
