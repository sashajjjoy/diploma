from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db.models import Q, Sum
from django.contrib.auth.models import User
from datetime import timedelta

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('client', 'Клиент'),
        ('operator', 'Оператор столовой'),
        ('admin', 'Администратор'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField('Роль', max_length=20, choices=ROLE_CHOICES, default='client')
    phone = models.CharField('Телефон', max_length=20, blank=True, null=True)
    
    class Meta:
        verbose_name = 'Профиль пользователя'
        verbose_name_plural = 'Профили пользователей'
    
    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
    
    def is_client(self):
        return self.role == 'client'
    
    def is_operator(self):
        return self.role == 'operator'
    
    def is_admin(self):
        return self.role == 'admin' or self.user.is_superuser


class Client(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_profile', blank=True, null=True)
    full_name = models.CharField('ФИО', max_length=255)
    email = models.EmailField('Email', unique=True)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)

    class Meta:
        verbose_name = 'Клиент'
        verbose_name_plural = 'Клиенты'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.full_name} ({self.email})"

    def clean(self):
        if self.email:
            self.email = self.email.lower()

    def delete(self, using=None, keep_parents=False):
        if self.reservations.exists():
            raise ValidationError(
                'Невозможно удалить клиента с наличием связанных бронирований'
            )
        return super().delete(using=using, keep_parents=keep_parents)


class Table(models.Model):
    table_number = models.CharField('Номер столика', max_length=50, unique=True)
    seats = models.PositiveIntegerField(
        'Количество мест',
        validators=[MinValueValidator(1)]
    )
    
    def clean(self):
        if self.seats > 4:
            raise ValidationError('Столик не может вмещать более 4 человек.')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Столик'
        verbose_name_plural = 'Столики'
        ordering = ['table_number']
        constraints = [
            models.CheckConstraint(
                check=models.Q(seats__gt=0) & models.Q(seats__lte=4),
                name='table_seats_between_1_and_4'
            ),
        ]

    def __str__(self):
        return f"Столик №{self.table_number} ({self.seats} мест)"

    def delete(self, using=None, keep_parents=False):
        if self.reservations.exists():
            active_reservations = self.reservations.filter(
                end_time__gte=timezone.now()
            )
            if active_reservations.exists():
                raise ValidationError(
                    'Невозможно удалить столик с активными или будущими бронированиями'
                )
        return super().delete(using=using, keep_parents=keep_parents)


class Dish(models.Model):
    name = models.CharField('Название', max_length=50)
    image = models.ImageField('Изображение', upload_to='dishes/', blank=True, null=True)
    description = models.TextField('Описание', blank=True, null=True)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2, default=0.00, validators=[MinValueValidator(0)])
    available_quantity = models.PositiveIntegerField(
        'Доступное количество',
        default=0,
        validators=[MinValueValidator(0)]
    )

    class Meta:
        verbose_name = 'Блюдо'
        verbose_name_plural = 'Блюда'
        ordering = ['name']

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        if self.reservation_dishes.exists():
            raise ValidationError(
                'Невозможно удалить блюдо, которое используется в бронированиях'
            )
        return super().delete(using=using, keep_parents=keep_parents)


class Reservation(models.Model):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='reservations',
        verbose_name='Клиент'
    )
    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name='reservations',
        verbose_name='Столик'
    )
    guests_count = models.PositiveIntegerField(
        'Количество персон',
        validators=[MinValueValidator(1)]
    )
    start_time = models.DateTimeField('Дата и время начала')
    end_time = models.DateTimeField('Дата и время окончания')
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)

    class Meta:
        verbose_name = 'Бронирование'
        verbose_name_plural = 'Бронирования'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['table', 'start_time', 'end_time']),
            models.Index(fields=['client']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F('start_time')),
                name='reservation_end_after_start'
            ),
            models.CheckConstraint(
                check=models.Q(guests_count__gt=0),
                name='reservation_guests_positive'
            ),
        ]

    def __str__(self):
        return f"Бронирование {self.table.table_number} на {self.start_time.strftime('%d.%m.%Y %H:%M')}"

    def get_working_days_until(self, target_date):
        """Подсчитывает количество рабочих дней до указанной даты (понедельник-пятница)"""
        from datetime import date
        from django.utils import timezone as tz
        now = tz.localtime(tz.now())  # Используем московское время
        now_date = now.date()
        target_date_only = target_date.date() if hasattr(target_date, 'date') else target_date
        
        if now_date >= target_date_only:
            return 0
        
        working_days = 0
        current = now_date
        target = target_date_only
        
        while current < target:
            # 0 = понедельник, 4 = пятница
            if current.weekday() < 5:  # Понедельник-пятница
                working_days += 1
            current += timedelta(days=1)
        
        return working_days
    
    def can_modify_or_cancel(self):
        """Проверяет, можно ли изменить или отменить бронирование (не позже чем за 30 минут)"""
        from django.utils import timezone as tz
        now = tz.localtime(tz.now())  # Используем московское время
        if self.start_time <= now:
            return False
        time_until_start = self.start_time - now
        return time_until_start >= timedelta(minutes=30)

    def clean(self):
        errors = {}

        # Проверка: дата окончания > даты начала
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                errors['end_time'] = 'Дата окончания должна быть позже даты начала'

        # Проверка: количество персон <= количеству мест столика
        if self.table and self.guests_count:
            if self.guests_count > self.table.seats:
                errors['guests_count'] = (
                    f'Количество персон ({self.guests_count}) не может превышать '
                    f'количество мест столика ({self.table.seats})'
                )

        # Проверка: бронирование не позже чем через 2 рабочих дня
        if self.start_time:
            working_days = self.get_working_days_until(self.start_time)
            if working_days > 2:
                errors['start_time'] = (
                    'Бронирование можно сделать максимум за 2 рабочих дня. '
                    f'До выбранной даты {working_days} рабочих дней (максимум 2).'
                )

        # Проверка пересечений бронирований для одного столика
        if self.table and self.start_time and self.end_time:
            # Поиск пересекающихся бронирований для того же столика
            overlapping_query = Reservation.objects.filter(
                table=self.table
            ).filter(
                Q(start_time__lt=self.end_time) & Q(end_time__gt=self.start_time)
            )
            
            # При редактировании исключаем текущую запись
            if self.pk:
                overlapping_query = overlapping_query.exclude(pk=self.pk)

            overlapping = overlapping_query.exists()

            if overlapping:
                # Получаем информацию о пересекающихся бронированиях для более информативного сообщения
                overlapping_reservations = overlapping_query.all()[:3]  # Берем первые 3 для примера
                reservation_times = [
                    f"{r.start_time.strftime('%d.%m.%Y %H:%M')} - {r.end_time.strftime('%H:%M')}"
                    for r in overlapping_reservations
                ]
                times_str = ", ".join(reservation_times)
                if overlapping_query.count() > 3:
                    times_str += f" и ещё {overlapping_query.count() - 3}"
                
                errors['table'] = (
                    f'Столик №{self.table.table_number} уже забронирован на выбранное время. '
                    f'Занятые времена: {times_str}. '
                    f'Пожалуйста, выберите другое время или другой столик.'
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        """Вызов clean перед сохранением"""
        self.full_clean()
        super().save(*args, **kwargs)


class ReservationDish(models.Model):
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name='dishes',
        verbose_name='Бронирование'
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name='reservation_dishes',
        verbose_name='Блюдо'
    )
    quantity = models.PositiveIntegerField(
        'Количество',
        validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = 'Позиция предзаказа'
        verbose_name_plural = 'Позиции предзаказа'
        unique_together = [['reservation', 'dish']]
        ordering = ['dish__name']
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name='reservation_dish_quantity_positive'
            ),
        ]

    def __str__(self):
        return f"{self.dish.name} x{self.quantity} (Бронирование #{self.reservation.id})"

    def clean(self):
        """Валидация на уровне модели"""
        errors = {}

        # Проверка: количество не должно превышать доступное количество блюда
        if self.dish and self.quantity:
            # Вычисляем уже зарезервированное количество в активных/будущих бронированиях
            if self.pk:
                # При редактировании исключаем текущую запись
                reserved = ReservationDish.objects.filter(
                    dish=self.dish
                ).exclude(
                    pk=self.pk
                ).filter(
                    reservation__end_time__gte=timezone.now()
                ).aggregate(
                    total=Sum('quantity')
                )['total'] or 0
                
                # Получаем старое количество текущей записи
                old_quantity = ReservationDish.objects.get(pk=self.pk).quantity
                available = self.dish.available_quantity - reserved + old_quantity
            else:
                # При создании новой записи учитываем все резервирования в активных/будущих бронированиях
                reserved = ReservationDish.objects.filter(
                    dish=self.dish,
                    reservation__end_time__gte=timezone.now()
                ).aggregate(
                    total=Sum('quantity')
                )['total'] or 0
                available = self.dish.available_quantity - reserved

            if self.quantity > available:
                errors['quantity'] = (
                    f'Недостаточно блюда. Доступно: {available}, '
                    f'запрошено: {self.quantity}'
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

