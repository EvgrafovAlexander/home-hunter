# Добавляет независимую историю исключений, не меняя объявления и оценки.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0070_viewing_description_source_links'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketExclusion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(choices=[('unfinished', 'Без отделки'), ('major_repair', 'Требует капитального ремонта'), ('bad_data', 'Ошибка данных'), ('duplicate', 'Дубликат'), ('other', 'Другое')], max_length=20)),
                ('comment', models.TextField(blank=True, max_length=2000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('is_active', models.BooleanField(default=True)),
                ('restored_at', models.DateTimeField(blank=True, null=True)),
                ('excluded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='market_exclusions', to=settings.AUTH_USER_MODEL)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='market_exclusions', to='listings.listing')),
                ('restored_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='restored_market_exclusions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created_at', '-pk'),
                'constraints': [models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('listing',), name='unique_active_market_exclusion')],
            },
        ),
    ]
