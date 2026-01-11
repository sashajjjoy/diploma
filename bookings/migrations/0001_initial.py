# Generated migration for initial models

from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Client',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(max_length=255, verbose_name='ФИО')),
                ('email', models.EmailField(max_length=254, unique=True, verbose_name='Email')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
            ],
            options={
                'verbose_name': 'Клиент',
                'verbose_name_plural': 'Клиенты',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Dish',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название')),
                ('image', models.ImageField(blank=True, null=True, upload_to='dishes/', verbose_name='Изображение')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Описание')),
                ('available_quantity', models.PositiveIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)], verbose_name='Доступное количество')),
            ],
            options={
                'verbose_name': 'Блюдо',
                'verbose_name_plural': 'Блюда',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Table',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('table_number', models.CharField(max_length=50, unique=True, verbose_name='Номер столика')),
                ('seats', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)], verbose_name='Количество мест')),
            ],
            options={
                'verbose_name': 'Столик',
                'verbose_name_plural': 'Столики',
                'ordering': ['table_number'],
            },
        ),
        migrations.CreateModel(
            name='Reservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('guests_count', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)], verbose_name='Количество персон')),
                ('start_time', models.DateTimeField(verbose_name='Дата и время начала')),
                ('end_time', models.DateTimeField(verbose_name='Дата и время окончания')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reservations', to='bookings.client', verbose_name='Клиент')),
                ('table', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reservations', to='bookings.table', verbose_name='Столик')),
            ],
            options={
                'verbose_name': 'Бронирование',
                'verbose_name_plural': 'Бронирования',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ReservationDish',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1)], verbose_name='Количество')),
                ('dish', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reservation_dishes', to='bookings.dish', verbose_name='Блюдо')),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dishes', to='bookings.reservation', verbose_name='Бронирование')),
            ],
            options={
                'verbose_name': 'Позиция предзаказа',
                'verbose_name_plural': 'Позиции предзаказа',
                'ordering': ['dish__name'],
            },
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['table', 'start_time', 'end_time'], name='bookings_re_table_i_123456_idx'),
        ),
        migrations.AddIndex(
            model_name='reservation',
            index=models.Index(fields=['client'], name='bookings_re_client__123456_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='reservationdish',
            unique_together={('reservation', 'dish')},
        ),
        migrations.AddConstraint(
            model_name='table',
            constraint=models.CheckConstraint(check=models.Q(seats__gt=0), name='table_seats_positive'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(check=models.Q(end_time__gt=models.F('start_time')), name='reservation_end_after_start'),
        ),
        migrations.AddConstraint(
            model_name='reservation',
            constraint=models.CheckConstraint(check=models.Q(guests_count__gt=0), name='reservation_guests_positive'),
        ),
        migrations.AddConstraint(
            model_name='reservationdish',
            constraint=models.CheckConstraint(check=models.Q(quantity__gt=0), name='reservation_dish_quantity_positive'),
        ),
    ]






