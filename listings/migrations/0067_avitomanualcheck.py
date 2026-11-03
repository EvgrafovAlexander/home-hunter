# История ручных проверок Avito без изменения существующих объявлений.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0066_cian_checkpoint_safety'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AvitoManualCheck',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('available', 'Доступно'), ('unavailable', 'Недоступно'), ('price', 'Обновить цену')], max_length=16)),
                ('checked_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('old_price', models.BigIntegerField(null=True)),
                ('new_price', models.BigIntegerField(null=True)),
                ('operation_id', models.UUIDField(unique=True)),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='avito_manual_checks', to='listings.listing')),
            ],
            options={
                'ordering': ('-checked_at', '-pk'),
                'indexes': [models.Index(fields=['listing', '-checked_at'], name='avito_check_listing_time')],
            },
        ),
    ]
