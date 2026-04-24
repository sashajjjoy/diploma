from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from django.utils import timezone

from .models import (
    Booking,
    CustomerOrder,
    Dish,
    News,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    Promotion,
    PromotionComboItem,
    PromotionDishRule,
    Table,
    UserProfile,
    VenueComplaint,
    WeeklyMenu,
    WeeklyMenuDay,
    WeeklyMenuDayItem,
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Профиль"
    fields = ("role", "phone")


class CustomUserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", "first_name", "last_name", "get_role", "is_active", "date_joined")
    list_filter = ("is_active", "is_superuser", "date_joined")

    def get_role(self, obj):
        try:
            profile = obj.profile
            if obj.is_superuser:
                return format_html('<span style="color: red; font-weight: bold;">Администратор</span>')
            return profile.get_role_display()
        except UserProfile.DoesNotExist:
            return "-"

    get_role.short_description = "Роль"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("profile")


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    raw_id_fields = ("dish",)


class OrderAppliedPromotionInline(admin.TabularInline):
    model = OrderAppliedPromotion
    extra = 0
    raw_id_fields = ("promotion",)


class PromotionComboItemInline(admin.TabularInline):
    model = PromotionComboItem
    extra = 1
    fk_name = "promotion"


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("id", "table_number", "seats", "is_available", "active_bookings")
    search_fields = ("table_number",)
    ordering = ("table_number",)

    def is_available(self, obj):
        now = timezone.now()
        has_active = obj.bookings.exclude(status=Booking.STATUS_CANCELLED).filter(
            start_time__lte=now,
            end_time__gte=now,
        ).exists()
        return format_html(
            '<span style="color: {};">{}</span>',
            "red" if has_active else "green",
            "Занят" if has_active else "Свободен",
        )

    is_available.short_description = "Текущий статус"

    def active_bookings(self, obj):
        return obj.bookings.exclude(status=Booking.STATUS_CANCELLED).filter(end_time__gte=timezone.now()).count()

    active_bookings.short_description = "Активных бронирований"


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "image_preview", "price", "available_quantity")
    search_fields = ("name", "description")
    ordering = ("name",)

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-width: 100px; max-height: 100px;" />', obj.image.url)
        return "Нет изображения"

    image_preview.short_description = "Изображение"


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "user", "table", "guests_count", "start_time", "end_time", "status")
    list_filter = ("status", "table")
    search_fields = ("public_id", "user__username", "table__table_number")
    raw_id_fields = ("user", "table")


@admin.register(CustomerOrder)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "user", "booking", "order_type", "scheduled_for", "status", "total_amount")
    list_filter = ("order_type", "status")
    search_fields = ("public_id", "user__username")
    raw_id_fields = ("user", "booking")
    inlines = [OrderItemInline, OrderAppliedPromotionInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "order", "dish_name_snapshot", "quantity", "unit_price_snapshot", "line_total_snapshot")
    raw_id_fields = ("order", "dish")


@admin.register(OrderAppliedPromotion)
class OrderAppliedPromotionAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "promotion", "discount_amount_snapshot")
    raw_id_fields = ("order", "promotion")


@admin.register(OrderItemReview)
class OrderItemReviewAdmin(admin.ModelAdmin):
    list_display = ("id", "order_item", "rating", "created_at")
    raw_id_fields = ("order_item",)


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "discount_type", "discount_value", "valid_from", "valid_to", "is_active")
    list_filter = ("is_active", "kind", "discount_type")
    inlines = [PromotionComboItemInline]
    raw_id_fields = ("target_dish",)


@admin.register(PromotionDishRule)
class PromotionDishRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "promotion", "dish", "rule_role", "min_quantity", "sort_order")
    list_filter = ("rule_role",)
    raw_id_fields = ("promotion", "dish")


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ("title", "is_published", "published_at", "updated_at")
    list_filter = ("is_published",)
    search_fields = ("title", "summary")


@admin.register(VenueComplaint)
class VenueComplaintAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("subject", "message", "user__username")


@admin.register(WeeklyMenu)
class WeeklyMenuAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(WeeklyMenuDay)
class WeeklyMenuDayAdmin(admin.ModelAdmin):
    list_display = ("id", "weekly_menu", "day_of_week", "is_active")
    list_filter = ("weekly_menu", "day_of_week", "is_active")


@admin.register(WeeklyMenuDayItem)
class WeeklyMenuDayItemAdmin(admin.ModelAdmin):
    list_display = ("id", "weekly_menu_day", "dish", "sort_order")
    list_filter = ("weekly_menu_day__weekly_menu", "weekly_menu_day__day_of_week")
    raw_id_fields = ("weekly_menu_day", "dish")
