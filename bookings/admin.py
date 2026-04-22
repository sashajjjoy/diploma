from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.utils import timezone
from .models import (
    Table,
    Dish,
    Reservation,
    ReservationDish,
    UserProfile,
    News,
    VenueComplaint,
    DishReview,
    Promotion,
    PromotionComboItem,
    ReservationAppliedPromotion,
)
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Профиль'
    fields = ('role', 'phone')


class CustomUserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'get_role', 'is_active', 'date_joined')
    list_filter = ('is_active', 'is_superuser', 'date_joined')
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Персональная информация', {'fields': ('first_name', 'last_name', 'email')}),
        ('Разрешения', {'fields': ('is_active', 'is_superuser', 'groups', 'user_permissions')}),
        ('Важные даты', {'fields': ('last_login', 'date_joined')}),
    )
    
    def get_role(self, obj):
        try:
            profile = obj.profile
            role_map = {
                'client': 'Клиент',
                'operator': 'Оператор столовой',
                'admin': 'Администратор',
            }
            if obj.is_superuser:
                return format_html('<span style="color: red; font-weight: bold;">Администратор</span>')
            return role_map.get(profile.role, profile.role)
        except UserProfile.DoesNotExist:
            return '-'
    get_role.short_description = 'Роль'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('profile')

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

class ReservationDishInline(admin.TabularInline):
    model = ReservationDish
    extra = 1
    fields = ['dish', 'quantity']
    verbose_name = 'Блюдо'
    verbose_name_plural = 'Блюда в предзаказе'


class ReservationAppliedPromotionInline(admin.TabularInline):
    model = ReservationAppliedPromotion
    extra = 0
    raw_id_fields = ['promotion']


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['id', 'table_number', 'seats', 'is_available', 'active_reservations']
    list_display_links = ['id', 'table_number']
    search_fields = ['table_number']
    list_filter = ['seats']
    ordering = ['table_number']

    fieldsets = (
        ('Информация о столике', {
            'fields': ('table_number', 'seats'),
            'description': 'Максимальное количество мест: 4'
        }),
    )

    def is_available(self, obj):
        now = timezone.now()
        has_active = obj.reservations.filter(
            start_time__lte=now,
            end_time__gte=now
        ).exists()
        if has_active:
            return format_html('<span style="color: red;">Занят</span>')
        return format_html('<span style="color: green;">Свободен</span>')
    is_available.short_description = 'Текущий статус'

    def active_reservations(self, obj):
        count = obj.reservations.filter(end_time__gte=timezone.now()).count()
        if count > 0:
            return format_html('<span style="color: orange;">{}</span>', count)
        return count
    active_reservations.short_description = 'Активных бронирований'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related('reservations')


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'image_preview', 'price', 'available_quantity', 'is_available']
    list_display_links = ['id', 'name']
    search_fields = ['name', 'description']
    list_filter = ['available_quantity']
    ordering = ['name']

    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'image', 'description')
        }),
        ('Цена и количество', {
            'fields': ('price', 'available_quantity',)
        }),
    )

    def image_preview(self, obj):
        """Предпросмотр изображения"""
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width: 100px; max-height: 100px;" />',
                obj.image.url
            )
        return 'Нет изображения'
    image_preview.short_description = 'Изображение'

    def is_available(self, obj):
        """Проверка доступности блюда"""
        if obj.available_quantity > 0:
            return format_html('<span style="color: green;">Доступно</span>')
        return format_html('<span style="color: red;">Нет в наличии</span>')
    is_available.short_description = 'Статус'


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    """Админка для бронирований"""
    list_display = [
        'id', 'table', 'user', 'guests_count', 'start_time',
        'end_time', 'duration', 'status', 'dishes_count'
    ]
    list_display_links = ['id', 'table']
    list_filter = [
        ('start_time', admin.DateFieldListFilter),
        'created_at', 'table',
    ]
    search_fields = [
        'user__username', 'user__email', 'user__first_name', 'user__last_name',
        'table__table_number'
    ]
    readonly_fields = ['created_at', 'total_dishes_info']
    date_hierarchy = 'start_time'
    ordering = ['-start_time']
    inlines = [ReservationAppliedPromotionInline, ReservationDishInline]

    fieldsets = (
        ('Информация о бронировании', {
            'fields': ('user', 'table', 'guests_count')
        }),
        ('Время бронирования', {
            'fields': ('start_time', 'end_time')
        }),
        ('Акция и суммы', {
            'fields': (
                'applied_promotion',
                'promotion_discount_total',
                'order_subtotal',
                'order_total',
            ),
        }),
        ('Предзаказ блюд', {
            'fields': ('total_dishes_info',),
            'classes': ('collapse',)
        }),
        ('Системная информация', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def duration(self, obj):
        """Длительность бронирования"""
        if obj.start_time and obj.end_time:
            delta = obj.end_time - obj.start_time
            hours = delta.total_seconds() / 3600
            return f"{hours:.1f} ч"
        return '-'
    duration.short_description = 'Длительность'

    def duration_display(self, obj):
        """Отображение длительности в детальном виде"""
        if obj.start_time and obj.end_time:
            delta = obj.end_time - obj.start_time
            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            return f"{hours} ч {minutes} мин"
        return '-'
    duration_display.short_description = 'Длительность'
    duration_display.readonly = True

    def status(self, obj):
        now = timezone.now()
        if obj.end_time < now:
            return format_html('<span style="color: gray;">Завершено</span>')
        elif obj.start_time <= now <= obj.end_time:
            return format_html('<span style="color: green;">Активно</span>')
        else:
            return format_html('<span style="color: blue;">Запланировано</span>')
    status.short_description = 'Статус'

    def dishes_count(self, obj):
        """Количество блюд в предзаказе"""
        count = obj.dishes.count()
        if count > 0:
            total_quantity = sum(d.quantity for d in obj.dishes.all())
            return f"{count} ({total_quantity} шт.)"
        return '0'
    dishes_count.short_description = 'Блюда в заказе'

    def total_dishes_info(self, obj):
        if obj.pk:
            dishes = obj.dishes.select_related('dish').all()
            if dishes.exists():
                html = '<ul>'
                for res_dish in dishes:
                    html += f'<li>{res_dish.dish.name} x{res_dish.quantity}</li>'
                html += '</ul>'
                return format_html(html)
        return 'Сохраните бронирование для добавления блюд'
    total_dishes_info.short_description = 'Список блюд'

    def get_queryset(self, request):
        """Оптимизация запросов"""
        qs = super().get_queryset(request)
        return qs.select_related('user', 'table').prefetch_related('dishes__dish')

    def save_model(self, request, obj, form, change):
        """Сохранение с валидацией"""
        # Валидация будет выполнена в методе save модели
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """Сохранение инлайнов с валидацией"""
        instances = formset.save(commit=False)
        for instance in instances:
            instance.full_clean()
            instance.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(ReservationDish)
class ReservationDishAdmin(admin.ModelAdmin):
    """Админка для позиций предзаказа (для отдельного управления)"""
    list_display = ['id', 'reservation', 'dish', 'quantity', 'reservation_status']
    list_display_links = ['id', 'reservation']
    list_filter = ['dish', ('reservation__start_time', admin.DateFieldListFilter)]
    search_fields = [
        'dish__name', 'reservation__user__username', 'reservation__user__first_name', 'reservation__user__last_name',
        'reservation__table__table_number'
    ]
    raw_id_fields = ['reservation', 'dish']
    ordering = ['-reservation__start_time']

    fieldsets = (
        ('Информация о позиции', {
            'fields': ('reservation', 'dish', 'quantity')
        }),
    )

    def reservation_status(self, obj):
        """Статус бронирования"""
        if obj.reservation:
            now = timezone.now()
            if obj.reservation.end_time < now:
                return format_html('<span style="color: gray;">Завершено</span>')
            elif obj.reservation.start_time <= now <= obj.reservation.end_time:
                return format_html('<span style="color: green;">Активно</span>')
            else:
                return format_html('<span style="color: blue;">Запланировано</span>')
        return '-'
    reservation_status.short_description = 'Статус бронирования'

    def get_queryset(self, request):
        """Оптимизация запросов"""
        qs = super().get_queryset(request)
        return qs.select_related('reservation', 'reservation__user', 'reservation__table', 'dish')


class PromotionComboItemInline(admin.TabularInline):
    model = PromotionComboItem
    extra = 1
    fk_name = 'promotion'


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'discount_type', 'discount_value', 'valid_from', 'valid_to', 'is_active')
    list_filter = ('is_active', 'kind', 'discount_type')
    inlines = [PromotionComboItemInline]
    raw_id_fields = ('target_dish',)


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_published', 'published_at', 'updated_at')
    list_filter = ('is_published',)
    search_fields = ('title', 'summary')


@admin.register(VenueComplaint)
class VenueComplaintAdmin(admin.ModelAdmin):
    list_display = ('subject', 'user', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('subject', 'message', 'user__username')
    readonly_fields = ('user', 'subject', 'message', 'created_at')


@admin.register(DishReview)
class DishReviewAdmin(admin.ModelAdmin):
    list_display = ('dish', 'user', 'rating', 'created_at', 'reservation_dish')
    list_filter = ('rating',)
    raw_id_fields = ('reservation_dish', 'user', 'dish')

