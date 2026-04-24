from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class UserProfile(models.Model):
    ROLE_CLIENT = "client"
    ROLE_OPERATOR = "operator"
    ROLE_ADMIN = "admin"
    ROLE_CHOICES = [
        (ROLE_CLIENT, "Client"),
        (ROLE_OPERATOR, "Operator"),
        (ROLE_ADMIN, "Admin"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CLIENT)
    phone = models.CharField(max_length=20, blank=True, null=True)

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    def is_client(self):
        return self.role == self.ROLE_CLIENT

    def is_operator(self):
        return self.role == self.ROLE_OPERATOR

    def is_admin(self):
        return self.role == self.ROLE_ADMIN or self.user.is_superuser


class Table(models.Model):
    table_number = models.CharField(max_length=50, unique=True)
    seats = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Table"
        verbose_name_plural = "Tables"
        ordering = ["table_number"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(seats__gt=0) & models.Q(seats__lte=4),
                name="table_seats_between_1_and_4",
            ),
        ]

    def __str__(self):
        return f"Table {self.table_number} ({self.seats} seats)"

    def clean(self):
        if self.seats > 4:
            raise ValidationError("A table cannot have more than 4 seats.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        if self.bookings.exists():
            active_bookings = self.bookings.filter(end_time__gte=timezone.now()).exclude(
                status=Booking.STATUS_CANCELLED
            )
            if active_bookings.exists():
                raise ValidationError("Cannot delete a table with active or future reservations.")
        return super().delete(using=using, keep_parents=keep_parents)


class Dish(models.Model):
    name = models.CharField(max_length=50)
    image = models.ImageField(upload_to="dishes/", blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    available_quantity = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        verbose_name = "Dish"
        verbose_name_plural = "Dishes"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        if self.order_items.exists():
            raise ValidationError("Cannot delete a dish that is referenced by orders.")
        return super().delete(using=using, keep_parents=keep_parents)


class WeeklyMenuDaySettings(models.Model):
    DAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    day_of_week = models.IntegerField(choices=DAY_CHOICES, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Weekly menu day settings"
        verbose_name_plural = "Weekly menu day settings"
        ordering = ["day_of_week"]

    def __str__(self):
        return f"Menu for {self.get_day_of_week_display()}"


class WeeklyMenuItem(models.Model):
    day_settings = models.ForeignKey(
        WeeklyMenuDaySettings,
        on_delete=models.CASCADE,
        related_name="items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="weekly_menu_items",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Weekly menu item"
        verbose_name_plural = "Weekly menu items"
        unique_together = [["day_settings", "dish"]]
        ordering = ["order", "dish__name"]

    def __str__(self):
        return f"{self.dish.name} - {self.day_settings.get_day_of_week_display()}"


class WeeklyMenu(models.Model):
    name = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Weekly menu"
        verbose_name_plural = "Weekly menus"
        ordering = ["name"]

    def __str__(self):
        return self.name


class WeeklyMenuDay(models.Model):
    weekly_menu = models.ForeignKey(
        WeeklyMenu,
        on_delete=models.CASCADE,
        related_name="days",
    )
    day_of_week = models.IntegerField(choices=WeeklyMenuDaySettings.DAY_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Weekly menu day"
        verbose_name_plural = "Weekly menu days"
        ordering = ["weekly_menu__name", "day_of_week"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekly_menu", "day_of_week"],
                name="weekly_menu_day_unique_per_menu",
            ),
        ]

    def __str__(self):
        return f"{self.weekly_menu.name}: {self.get_day_of_week_display()}"


class WeeklyMenuDayItem(models.Model):
    weekly_menu_day = models.ForeignKey(
        WeeklyMenuDay,
        on_delete=models.CASCADE,
        related_name="items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="weekly_menu_day_items",
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Weekly menu day item"
        verbose_name_plural = "Weekly menu day items"
        ordering = ["sort_order", "dish__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["weekly_menu_day", "dish"],
                name="weekly_menu_day_item_unique_dish",
            ),
        ]

    def __str__(self):
        return f"{self.weekly_menu_day}: {self.dish.name}"


class MenuOverride(models.Model):
    MODE_PATCH = "patch"
    MODE_REPLACE = "replace"
    MODE_CHOICES = [
        (MODE_PATCH, "Patch"),
        (MODE_REPLACE, "Replace"),
    ]

    weekly_menu = models.ForeignKey(
        "WeeklyMenu",
        on_delete=models.CASCADE,
        related_name="overrides",
        null=True,
        blank=True,
    )
    date_from = models.DateField()
    date_to = models.DateField(null=True, blank=True)
    priority = models.PositiveIntegerField(default=0)
    override_mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_PATCH)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Menu override"
        verbose_name_plural = "Menu overrides"
        ordering = ["-priority", "-date_from", "-id"]
        indexes = [
            models.Index(fields=["date_from", "date_to"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(priority__gte=0),
                name="menu_override_priority_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(date_to__isnull=True) | models.Q(date_to__gte=models.F("date_from")),
                name="menu_override_date_range_valid",
            ),
        ]

    def __str__(self):
        if self.date_to:
            return f"Menu override {self.date_from} - {self.date_to}"
        return f"Menu override {self.date_from}"

    def clean(self):
        if self.date_to and self.date_to < self.date_from:
            raise ValidationError("Override end date cannot be earlier than start date.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MenuOverrideItem(models.Model):
    override = models.ForeignKey(
        MenuOverride,
        on_delete=models.CASCADE,
        related_name="items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="menu_override_items",
    )
    action = models.CharField(
        max_length=10,
        choices=[("add", "Add"), ("remove", "Remove")],
        default="add",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Menu override item"
        verbose_name_plural = "Menu override items"
        unique_together = [["override", "dish"]]
        ordering = ["order", "dish__name"]

    def __str__(self):
        return f"{self.action} {self.dish.name} - {self.override}"


class News(models.Model):
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=500, blank=True)
    body = models.TextField()
    published_at = models.DateTimeField()
    is_published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "News"
        verbose_name_plural = "News"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-published_at"]),
        ]

    def __str__(self):
        return self.title


class Booking(models.Model):
    STATUS_SCHEDULED = "scheduled"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    table = models.ForeignKey(Table, on_delete=models.PROTECT, related_name="bookings")
    guests_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Booking"
        verbose_name_plural = "Bookings"
        ordering = ["-start_time"]
        indexes = [
            models.Index(fields=["table", "start_time", "end_time"]),
            models.Index(fields=["user", "start_time"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="booking_end_after_start",
            ),
            models.CheckConstraint(
                check=models.Q(guests_count__gt=0),
                name="booking_guests_positive",
            ),
        ]

    def __str__(self):
        return f"Booking #{self.public_id or self.pk}"

    def get_working_days_until(self, target_date):
        now = timezone.localtime(timezone.now())
        now_date = now.date()
        target_date_only = target_date.date() if hasattr(target_date, "date") else target_date
        if now_date >= target_date_only:
            return 0

        working_days = 0
        current = now_date
        while current < target_date_only:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)
        return working_days

    def can_modify_or_cancel(self):
        now = timezone.localtime(timezone.now())
        if self.start_time <= now:
            return False
        return (self.start_time - now) >= timedelta(minutes=30)

    def clean(self):
        errors = {}
        if self.end_time and self.start_time and self.end_time <= self.start_time:
            errors["end_time"] = "End time must be later than start time."
        if self.table and self.guests_count > self.table.seats:
            errors["guests_count"] = "Guests count cannot exceed the table capacity."
        if self.start_time:
            now_local = timezone.localtime(timezone.now())
            start_local = timezone.localtime(self.start_time)
            if now_local < start_local < now_local + timedelta(minutes=30):
                errors["start_time"] = "Booking must be created at least 30 minutes before the selected slot."
            working_days = self.get_working_days_until(self.start_time)
            if working_days > 2:
                errors["start_time"] = (
                    "Reservation can be made at most 2 working days ahead. "
                    f"Selected date is {working_days} working days away."
                )
        if self.table_id and self.start_time and self.end_time:
            overlapping_query = Booking.objects.filter(
                table_id=self.table_id,
            ).exclude(status=self.STATUS_CANCELLED).filter(Q(start_time__lt=self.end_time) & Q(end_time__gt=self.start_time))
            if self.pk:
                overlapping_query = overlapping_query.exclude(pk=self.pk)
            if overlapping_query.exists():
                errors["table"] = "This table is already booked for the selected time."
        if errors:
            raise ValidationError(errors)

    @property
    def order_subtotal(self):
        return self.order.subtotal_amount if hasattr(self, "order") else Decimal("0.00")

    @property
    def promotion_discount_total(self):
        return self.order.discount_total if hasattr(self, "order") else Decimal("0.00")

    @property
    def order_total(self):
        return self.order.total_amount if hasattr(self, "order") else Decimal("0.00")

    @property
    def applied_promotion(self):
        if hasattr(self, "order"):
            return self.order.applied_promotions.select_related("promotion").first()
        return None

    @property
    def applied_promotion_links(self):
        if hasattr(self, "order"):
            return self.order.applied_promotions.select_related("promotion")
        return OrderAppliedPromotion.objects.none()

    @property
    def dishes(self):
        if hasattr(self, "order"):
            return self.order.items.select_related("dish")
        return OrderItem.objects.none()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.public_id is None:
            self.public_id = self.pk
            super().save(update_fields=["public_id"])


class CustomerOrder(models.Model):
    TYPE_DINE_IN = "dine_in"
    TYPE_TAKEOUT = "takeout"
    TYPE_CHOICES = [
        (TYPE_DINE_IN, "Dine in"),
        (TYPE_TAKEOUT, "Takeout"),
    ]
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="customer_orders")
    booking = models.OneToOneField(
        Booking,
        on_delete=models.CASCADE,
        related_name="order",
        null=True,
        blank=True,
    )
    order_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    subtotal_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Customer order"
        verbose_name_plural = "Customer orders"
        ordering = ["-scheduled_for", "-created_at"]
        indexes = [
            models.Index(fields=["user", "scheduled_for"]),
            models.Index(fields=["status", "scheduled_for"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(subtotal_amount__gte=0),
                name="customer_order_subtotal_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(discount_total__gte=0),
                name="customer_order_discount_non_negative",
            ),
            models.CheckConstraint(
                check=models.Q(total_amount__gte=0),
                name="customer_order_total_non_negative",
            ),
        ]

    def __str__(self):
        return f"Order #{self.public_id or self.pk}"

    @property
    def table(self):
        return self.booking.table if self.booking_id else None

    @property
    def guests_count(self):
        return self.booking.guests_count if self.booking_id else 1

    @property
    def start_time(self):
        if self.booking_id:
            return self.booking.start_time
        return self.scheduled_for

    @property
    def end_time(self):
        if self.booking_id:
            return self.booking.end_time
        return self.scheduled_for + timedelta(days=1) - timedelta(minutes=1)

    @property
    def order_subtotal(self):
        return self.subtotal_amount

    @property
    def promotion_discount_total(self):
        return self.discount_total

    @property
    def order_total(self):
        return self.total_amount

    @property
    def applied_promotion(self):
        return self.applied_promotions.select_related("promotion").first()

    @property
    def applied_promotion_links(self):
        return self.applied_promotions.select_related("promotion")

    @property
    def dishes(self):
        return self.items.select_related("dish")

    def can_modify_or_cancel(self):
        boundary = self.start_time
        now = timezone.localtime(timezone.now())
        if boundary <= now:
            return False
        return (boundary - now) >= timedelta(minutes=30)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.public_id is None:
            self.public_id = self.pk
            super().save(update_fields=["public_id"])


class VenueComplaint(models.Model):
    STATUS_CHOICES = [
        ("new", "Новая"),
        ("seen", "Просмотрена"),
        ("closed", "Закрыта"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="venue_complaints")
    related_booking = models.ForeignKey(
        Booking,
        on_delete=models.SET_NULL,
        related_name="complaints",
        null=True,
        blank=True,
    )
    related_order = models.ForeignKey(
        CustomerOrder,
        on_delete=models.SET_NULL,
        related_name="complaints",
        null=True,
        blank=True,
    )
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Venue complaint"
        verbose_name_plural = "Venue complaints"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.subject} ({self.user.username})"


class Promotion(models.Model):
    KIND_SINGLE = "single_dish"
    KIND_COMBO = "combo"
    KIND_CHOICES = [
        (KIND_SINGLE, "Single dish"),
        (KIND_COMBO, "Combo"),
    ]
    DISCOUNT_PERCENT = "percent"
    DISCOUNT_FIXED_OFF = "fixed_off"
    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_PERCENT, "Percent"),
        (DISCOUNT_FIXED_OFF, "Fixed off"),
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES)
    discount_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent (0-100) or a fixed amount depending on discount type.",
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)
    target_dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="single_promotions",
    )

    class Meta:
        verbose_name = "Promotion"
        verbose_name_plural = "Promotions"
        ordering = ["-valid_from"]
        indexes = [
            models.Index(fields=["is_active", "valid_from", "valid_to"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(valid_to__gt=models.F("valid_from")),
                name="promotion_valid_to_after_valid_from",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.valid_to <= self.valid_from:
            raise ValidationError({"valid_to": "Promotion end time must be later than start time."})
        if self.kind == self.KIND_SINGLE and not self.target_dish_id:
            raise ValidationError({"target_dish": "Single-dish promotion requires a target dish."})
        if self.kind == self.KIND_COMBO and self.target_dish_id:
            raise ValidationError({"target_dish": "Combo promotion must not use target_dish."})
        if self.discount_type == self.DISCOUNT_PERCENT and self.discount_value > 100:
            raise ValidationError({"discount_value": "Percent discount cannot exceed 100."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PromotionComboItem(models.Model):
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="combo_items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="promotion_combo_entries",
    )
    min_quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Promotion combo item"
        verbose_name_plural = "Promotion combo items"
        unique_together = [["promotion", "dish"]]
        ordering = ["promotion", "dish__name"]

    def __str__(self):
        return f"{self.promotion.name}: {self.dish.name} x{self.min_quantity}"


class PromotionDishRule(models.Model):
    ROLE_REQUIRED = "required"
    ROLE_TARGET = "target"
    ROLE_CHOICES = [
        (ROLE_REQUIRED, "Required"),
        (ROLE_TARGET, "Target"),
    ]

    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name="dish_rules",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="promotion_rules",
    )
    rule_role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    min_quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Promotion dish rule"
        verbose_name_plural = "Promotion dish rules"
        ordering = ["sort_order", "dish__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["promotion", "dish", "rule_role"],
                name="promotion_rule_unique_role_per_dish",
            ),
        ]

    def __str__(self):
        return f"{self.promotion.name}: {self.dish.name}"


class OrderAppliedPromotion(models.Model):
    order = models.ForeignKey(
        CustomerOrder,
        on_delete=models.CASCADE,
        related_name="applied_promotions",
    )
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.PROTECT,
        related_name="order_applied_rows",
    )
    promotion_name_snapshot = models.CharField(max_length=255)
    quantity_applied = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    original_amount_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_amount_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Order applied promotion"
        verbose_name_plural = "Order applied promotions"
        ordering = ["promotion_name_snapshot"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "promotion"],
                name="order_applied_promotion_unique_per_order",
            ),
        ]

    def __str__(self):
        return f"{self.order_id}: {self.promotion_name_snapshot}"

    @property
    def discount_amount(self):
        return self.discount_amount_snapshot

    @property
    def original_amount(self):
        return self.original_amount_snapshot

    @property
    def discounted_amount(self):
        discounted = self.original_amount_snapshot - self.discount_amount_snapshot
        if discounted < 0:
            return Decimal("0.00")
        return discounted


class OrderItem(models.Model):
    public_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    order = models.ForeignKey(
        CustomerOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.PROTECT,
        related_name="order_items",
        null=True,
        blank=True,
    )
    dish_name_snapshot = models.CharField(max_length=255)
    unit_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    line_total_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Order item"
        verbose_name_plural = "Order items"
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "dish"],
                name="order_item_unique_dish_per_order",
            ),
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name="order_item_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.dish_name_snapshot} x{self.quantity}"

    @property
    def reservation(self):
        return self.order.booking or self.order

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.public_id is None:
            self.public_id = self.pk
            super().save(update_fields=["public_id"])


class OrderItemReview(models.Model):
    order_item = models.OneToOneField(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="review",
    )
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Order item review"
        verbose_name_plural = "Order item reviews"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_item}: {self.rating}"

    @property
    def user(self):
        return self.order_item.order.user

    @property
    def dish(self):
        return self.order_item.dish

    def clean(self):
        if self.order_item_id:
            if self.order_item.order.user_id != self.user.id:
                raise ValidationError("Only the order owner can leave a review.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
