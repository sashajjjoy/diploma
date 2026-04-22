from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.db.models import Q
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


# Модель Client удалена - теперь используем User с ролью через UserProfile


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
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='reservations',
        verbose_name='Пользователь'
    )
    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name='reservations',
        verbose_name='Столик',
        null=True,
        blank=True
    )
    guests_count = models.PositiveIntegerField(
        'Количество персон',
        validators=[MinValueValidator(1)]
    )
    start_time = models.DateTimeField('Дата и время начала')
    end_time = models.DateTimeField('Дата и время окончания')
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    applied_promotion = models.ForeignKey(
        'Promotion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservations',
        verbose_name='Применённая акция',
    )
    promotion_discount_total = models.DecimalField(
        'Сумма скидки по акции',
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    order_subtotal = models.DecimalField(
        'Сумма заказа до скидки',
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    order_total = models.DecimalField(
        'Итого к оплате',
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        verbose_name = 'Бронирование'
        verbose_name_plural = 'Бронирования'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['table', 'start_time', 'end_time']),
            models.Index(fields=['user']),
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
        if self.table:
            return f"Бронирование {self.table.table_number} на {self.start_time.strftime('%d.%m.%Y %H:%M')}"
        else:
            return f"Заказ на вынос на {self.start_time.strftime('%d.%m.%Y')}"

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


# ========== СИСТЕМА МЕНЮ ==========

class WeeklyMenuDaySettings(models.Model):
    """Настройки меню на день недели: активность и привязка позиций (0=понедельник, 6=воскресенье)."""
    DAY_CHOICES = [
        (0, 'Понедельник'),
        (1, 'Вторник'),
        (2, 'Среда'),
        (3, 'Четверг'),
        (4, 'Пятница'),
        (5, 'Суббота'),
        (6, 'Воскресенье'),
    ]

    day_of_week = models.IntegerField('День недели', choices=DAY_CHOICES, unique=True)
    is_active = models.BooleanField('Активно', default=True)

    class Meta:
        verbose_name = 'Настройки меню на день недели'
        verbose_name_plural = 'Настройки меню по дням недели'
        ordering = ['day_of_week']

    def __str__(self):
        return f"Меню на {self.get_day_of_week_display()}"


class WeeklyMenuItem(models.Model):
    """Блюдо в еженедельном меню на конкретный день недели."""
    day_settings = models.ForeignKey(
        WeeklyMenuDaySettings,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='День недели (настройки)'
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name='weekly_menu_items',
        verbose_name='Блюдо'
    )
    order = models.PositiveIntegerField('Порядок сортировки', default=0)

    class Meta:
        verbose_name = 'Блюдо в еженедельном меню'
        verbose_name_plural = 'Блюда в еженедельном меню'
        unique_together = [['day_settings', 'dish']]
        ordering = ['order', 'dish__name']

    def __str__(self):
        return f"{self.dish.name} - {self.day_settings.get_day_of_week_display()}"


class MenuOverride(models.Model):
    """Переопределение меню на конкретную дату или период"""
    date_from = models.DateField('Дата начала')
    date_to = models.DateField('Дата окончания', null=True, blank=True)
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)
    
    class Meta:
        verbose_name = 'Переопределение меню'
        verbose_name_plural = 'Переопределения меню'
        ordering = ['-date_from']
        indexes = [
            models.Index(fields=['date_from', 'date_to']),
        ]
    
    def __str__(self):
        if self.date_to:
            return f"Переопределение меню {self.date_from} - {self.date_to}"
        else:
            return f"Переопределение меню {self.date_from}"
    
    def clean(self):
        if self.date_to and self.date_to < self.date_from:
            raise ValidationError('Дата окончания не может быть раньше даты начала.')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MenuOverrideItem(models.Model):
    """Блюдо в переопределении меню"""
    override = models.ForeignKey(
        MenuOverride,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Переопределение'
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name='menu_override_items',
        verbose_name='Блюдо'
    )
    action = models.CharField(
        'Действие',
        max_length=10,
        choices=[('add', 'Добавить'), ('remove', 'Убрать')],
        default='add'
    )
    order = models.PositiveIntegerField('Порядок сортировки', default=0)
    
    class Meta:
        verbose_name = 'Блюдо в переопределении меню'
        verbose_name_plural = 'Блюда в переопределении меню'
        unique_together = [['override', 'dish']]
        ordering = ['order', 'dish__name']
    
    def __str__(self):
        action_text = 'Добавить' if self.action == 'add' else 'Убрать'
        return f"{action_text} {self.dish.name} - {self.override}"


# ========== НОВОСТИ, ЖАЛОБЫ, ОТЗЫВЫ, АКЦИИ ==========

class News(models.Model):
    title = models.CharField('Заголовок', max_length=200)
    summary = models.CharField('Кратко', max_length=500, blank=True)
    body = models.TextField('Текст')
    published_at = models.DateTimeField('Дата публикации')
    is_published = models.BooleanField('Опубликовано', default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Новость'
        verbose_name_plural = 'Новости'
        ordering = ['-published_at', '-created_at']

    def __str__(self):
        return self.title


class VenueComplaint(models.Model):
    STATUS_CHOICES = [
        ('new', 'Новая'),
        ('seen', 'Просмотрена'),
        ('closed', 'Закрыта'),
    ]
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='venue_complaints',
        verbose_name='Пользователь',
    )
    subject = models.CharField('Тема', max_length=200)
    message = models.TextField('Текст жалобы')
    status = models.CharField('Статус', max_length=20, choices=STATUS_CHOICES, default='new', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Жалоба на заведение'
        verbose_name_plural = 'Жалобы на заведение'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return f'{self.subject} ({self.user.username})'


class Promotion(models.Model):
    KIND_SINGLE = 'single_dish'
    KIND_COMBO = 'combo'
    KIND_CHOICES = [
        (KIND_SINGLE, 'Одно блюдо'),
        (KIND_COMBO, 'Комбо (набор блюд)'),
    ]
    DISCOUNT_PERCENT = 'percent'
    DISCOUNT_FIXED_OFF = 'fixed_off'
    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_PERCENT, 'Процент от суммы'),
        (DISCOUNT_FIXED_OFF, 'Фиксированная скидка (руб.)'),
    ]

    name = models.CharField('Название', max_length=200)
    description = models.TextField('Описание для клиента', blank=True)
    kind = models.CharField('Тип', max_length=20, choices=KIND_CHOICES)
    discount_type = models.CharField('Тип скидки', max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(
        'Значение скидки',
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Процент (0–100) или сумма в рублях в зависимости от типа',
    )
    valid_from = models.DateTimeField('Действует с')
    valid_to = models.DateTimeField('Действует до')
    is_active = models.BooleanField('Активна', default=True, db_index=True)
    target_dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='single_promotions',
        verbose_name='Блюдо (только для типа «одно блюдо»)',
    )

    class Meta:
        verbose_name = 'Акция'
        verbose_name_plural = 'Акции'
        ordering = ['-valid_from']
        indexes = [
            models.Index(fields=['is_active', 'valid_from', 'valid_to']),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.kind == self.KIND_SINGLE and not self.target_dish_id:
            raise ValidationError({'target_dish': 'Для акции на одно блюдо укажите блюдо.'})
        if self.kind == self.KIND_COMBO and self.target_dish_id:
            raise ValidationError({'target_dish': 'Для комбо-акции поле блюда не используется.'})
        if self.discount_type == self.DISCOUNT_PERCENT and self.discount_value > 100:
            raise ValidationError({'discount_value': 'Процент не может превышать 100.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PromotionComboItem(models.Model):
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name='combo_items',
        verbose_name='Акция',
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name='promotion_combo_entries',
        verbose_name='Блюдо в наборе',
    )
    min_quantity = models.PositiveIntegerField('Мин. количество', default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = 'Позиция комбо'
        verbose_name_plural = 'Позиции комбо'
        unique_together = [['promotion', 'dish']]
        ordering = ['promotion', 'dish__name']

    def __str__(self):
        return f'{self.promotion.name}: {self.dish.name} ×{self.min_quantity}'


class ReservationAppliedPromotion(models.Model):
    """Какие акции применены к заказу (каждая акция не более одного раза)."""

    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name='applied_promotion_links',
        verbose_name='Бронирование',
    )
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.PROTECT,
        related_name='reservation_applied_rows',
        verbose_name='Акция',
    )
    discount_amount = models.DecimalField(
        'Скидка по этой акции',
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    class Meta:
        verbose_name = 'Применённая акция'
        verbose_name_plural = 'Применённые акции'
        unique_together = [['reservation', 'promotion']]
        ordering = ['promotion__name']

    def __str__(self):
        return f'{self.reservation_id}: {self.promotion.name}'


class DishReview(models.Model):
    reservation_dish = models.OneToOneField(
        ReservationDish,
        on_delete=models.CASCADE,
        related_name='review',
        verbose_name='Строка заказа',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='dish_reviews',
        verbose_name='Пользователь',
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name='reviews',
        verbose_name='Блюдо',
    )
    rating = models.PositiveSmallIntegerField(
        'Оценка',
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment = models.TextField('Комментарий', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Отзыв о блюде'
        verbose_name_plural = 'Отзывы о блюдах'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.dish.name}: {self.rating}★'

    def clean(self):
        if self.reservation_dish_id:
            rd = self.reservation_dish
            if rd.dish_id != self.dish_id:
                raise ValidationError('Блюдо отзыва должно совпадать с блюдом в строке заказа.')
            if rd.reservation.user_id != self.user_id:
                raise ValidationError('Отзыв может оставить только автор заказа.')
