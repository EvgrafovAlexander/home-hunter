# Личные карточки квартир, записи просмотров и метаданные фотографий.

import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('listings', '0068_marketdailysnapshot'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ViewingApartment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200, verbose_name='Название')),
                ('address', models.CharField(blank=True, max_length=500, verbose_name='Адрес')),
                ('district', models.CharField(blank=True, max_length=255, verbose_name='Район')),
                ('rooms', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Комнат')),
                ('area', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True, validators=[django.core.validators.MinValueValidator(1)], verbose_name='Площадь, м²')),
                ('floor', models.IntegerField(blank=True, null=True, verbose_name='Этаж')),
                ('floors_total', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Этажей в доме')),
                ('asking_price', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Заявленная цена, ₽')),
                ('negotiated_price', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Обсуждаемая цена, ₽')),
                ('price_note', models.TextField(blank=True, verbose_name='О торге')),
                ('contact_name', models.CharField(blank=True, max_length=200, verbose_name='Контакт')),
                ('contact_phone', models.CharField(blank=True, max_length=60, verbose_name='Телефон')),
                ('contact_role', models.CharField(blank=True, choices=[('owner', 'Собственник'), ('agent', 'Агент'), ('other', 'Другое')], max_length=20, verbose_name='Кто показывает')),
                ('decision', models.CharField(choices=[('thinking', 'Думаю'), ('suitable', 'Подходит'), ('rejected', 'Отказ'), ('archived', 'Архив')], default='thinking', max_length=20, verbose_name='Решение')),
                ('rating', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(10)], verbose_name='Моя оценка / 10')),
                ('pros', models.TextField(blank=True, verbose_name='Плюсы')),
                ('cons', models.TextField(blank=True, verbose_name='Минусы')),
                ('questions', models.TextField(blank=True, verbose_name='Уточнить')),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='viewing_apartments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-updated_at', '-pk'),
            },
        ),
        migrations.CreateModel(
            name='ViewingEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('note', 'Комментарий'), ('visit', 'Просмотр')], default='note', max_length=10, verbose_name='Тип записи')),
                ('status', models.CharField(blank=True, choices=[('planned', 'Запланирован'), ('completed', 'Состоялся'), ('cancelled', 'Отменён')], max_length=10, verbose_name='Статус просмотра')),
                ('happened_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата и время просмотра')),
                ('body', models.TextField(blank=True, verbose_name='Комментарий')),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('apartment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='listings.viewingapartment')),
            ],
            options={
                'ordering': ('-created_at', '-pk'),
            },
        ),
        migrations.CreateModel(
            name='ViewingPhoto',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('image', models.FileField(upload_to='viewings/')),
                ('thumbnail', models.FileField(upload_to='viewings/')),
                ('caption', models.CharField(blank=True, max_length=200, verbose_name='Подпись')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('apartment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='photos', to='listings.viewingapartment')),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='photos', to='listings.viewingentry')),
            ],
            options={
                'ordering': ('created_at', 'pk'),
            },
        ),
        migrations.AddField(
            model_name='viewingapartment',
            name='cover_photo',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='listings.viewingphoto'),
        ),
        migrations.CreateModel(
            name='ViewingListingLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('apartment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listing_links', to='listings.viewingapartment')),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='viewing_links', to='listings.listing')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('apartment', 'listing'), name='unique_viewing_listing_link')],
            },
        ),
        migrations.AddIndex(
            model_name='viewingapartment',
            index=models.Index(fields=['owner', 'decision'], name='listings_vi_owner_i_08c1f9_idx'),
        ),
        migrations.AddConstraint(
            model_name='viewingapartment',
            constraint=models.CheckConstraint(condition=models.Q(('rating__isnull', True), models.Q(('rating__gte', 1), ('rating__lte', 10)), _connector='OR'), name='viewing_rating_1_to_10'),
        ),
    ]
