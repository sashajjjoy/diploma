# Generated migration to replace Client with User

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_client_to_user(apps, schema_editor):
    """Миграция данных: связываем Reservation с User через Client.user"""
    Reservation = apps.get_model('bookings', 'Reservation')
    Client = apps.get_model('bookings', 'Client')
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    # Обновляем все Reservation, связывая их с User через Client
    reservations_to_delete = []
    for reservation in Reservation.objects.all().select_related('client'):
        if reservation.client:
            user = None
            # Если у Client есть user, используем его
            if reservation.client.user_id:
                user = reservation.client.user
            else:
                # Если user нет, ищем по email
                try:
                    user = User.objects.get(email=reservation.client.email)
                except User.DoesNotExist:
                    # Если пользователь не найден, создаем нового
                    username = reservation.client.email.split('@')[0]
                    # Убеждаемся, что username уникален
                    counter = 1
                    base_username = username
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1
                    
                    user = User.objects.create(
                        username=username,
                        email=reservation.client.email
                    )
            
            if user:
                reservation.user = user
                reservation.save(update_fields=['user'])
            else:
                reservations_to_delete.append(reservation.id)
        else:
            reservations_to_delete.append(reservation.id)
    
    # Удаляем Reservation без user
    if reservations_to_delete:
        Reservation.objects.filter(id__in=reservations_to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('bookings', '0007_remove_userprofile_and_email'),
    ]

    operations = [
        # 1. Добавляем поле user в Reservation как nullable
        migrations.AddField(
            model_name='reservation',
            name='user',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reservations',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Пользователь'
            ),
        ),
        # 2. Мигрируем данные для Reservation
        migrations.RunPython(migrate_client_to_user, reverse_code=migrations.RunPython.noop),
        # 3. Делаем поле user обязательным в Reservation
        migrations.AlterField(
            model_name='reservation',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reservations',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Пользователь'
            ),
        ),
        # 4. Делаем table nullable в Reservation
        migrations.AlterField(
            model_name='reservation',
            name='table',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reservations',
                to='bookings.table',
                verbose_name='Столик'
            ),
        ),
        # 5. Удаляем поле client из Reservation
        migrations.RemoveField(
            model_name='reservation',
            name='client',
        ),
        # 6. Удаляем модель Client (операция удаления модели)
        migrations.DeleteModel(
            name='Client',
        ),
    ]
