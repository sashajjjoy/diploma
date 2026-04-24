from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from bookings.models import (
    Booking,
    CustomerOrder,
    Dish,
    MenuOverride,
    MenuOverrideItem,
    News,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    Promotion,
    PromotionComboItem,
    Table,
    UserProfile,
    VenueComplaint,
    WeeklyMenuDaySettings,
    WeeklyMenuItem,
)

User = get_user_model()

MIN_ROWS = 10


def clear_all_except_table_dish() -> None:
    Session.objects.all().delete()
    OrderItemReview.objects.all().delete()
    OrderAppliedPromotion.objects.all().delete()
    OrderItem.objects.all().delete()
    CustomerOrder.objects.all().delete()
    Booking.objects.all().delete()
    PromotionComboItem.objects.all().delete()
    Promotion.objects.all().delete()
    VenueComplaint.objects.all().delete()
    News.objects.all().delete()
    MenuOverrideItem.objects.all().delete()
    MenuOverride.objects.all().delete()
    WeeklyMenuItem.objects.all().delete()
    WeeklyMenuDaySettings.objects.all().delete()
    UserProfile.objects.all().delete()
    User.objects.all().delete()


def _ensure_tables_and_dishes():
    tables = list(Table.objects.order_by("id"))
    dishes = list(Dish.objects.order_by("id"))
    if not tables:
        raise RuntimeError("Table data is required before reseeding.")
    if not dishes:
        raise RuntimeError("Dish data is required before reseeding.")
    return tables, dishes


def _make_users():
    users = []
    for i in range(MIN_ROWS):
        user = User.objects.create_user(
            username=f"client{i + 1}",
            email=f"client{i + 1}@example.com",
            password="client123",
            first_name=f"Client{i + 1}",
            last_name="Test",
        )
        UserProfile.objects.create(user=user, role="client", phone=f"+7900123456{i % 10}")
        users.append(user)

    operator = User.objects.create_user(
        username="operator1",
        email="operator1@example.com",
        password="operator123",
        first_name="Operator",
        last_name="Demo",
    )
    UserProfile.objects.create(user=operator, role="operator")

    admin = User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password="admin123",
        first_name="Admin",
        is_staff=True,
        is_superuser=True,
    )
    UserProfile.objects.create(user=admin, role="admin")
    return users, operator, admin


def _booking_slot_past(index: int, tables: list[Table]):
    table_count = len(tables)
    day_offset = index // table_count + 1
    table_idx = index % table_count
    hour = 9 + (index % 6) * 2
    target_date = timezone.localdate() - timedelta(days=day_offset)
    start = timezone.make_aware(datetime.combine(target_date, time(hour, 0)))
    end = start + timedelta(hours=1, minutes=30)
    table = tables[table_idx]
    guests = min(2, table.seats)
    return table, start, end, guests


def _future_dates_within_two_working_days(need: int):
    out = []
    today = timezone.localdate()
    for add in range(0, 21):
        target_date = today + timedelta(days=add)
        if target_date.weekday() >= 5:
            continue
        noon = timezone.make_aware(datetime.combine(target_date, time(12, 0)))
        if Booking.get_working_days_until(noon) <= 2:
            out.append(target_date)
        if len(out) >= need:
            break
    return out


def _booking_slot_future(index: int, tables: list[Table], future_dates: list):
    table_count = len(tables)
    target_date = future_dates[index % len(future_dates)]
    hour = 10 + (index % 5) * 2
    table_idx = (index + (index // len(future_dates))) % table_count
    start = timezone.make_aware(datetime.combine(target_date, time(hour, 0)))
    end = start + timedelta(hours=1, minutes=30)
    table = tables[table_idx]
    guests = min(2 + (index % 2), table.seats)
    return table, start, end, guests


def _create_order_for_booking(user, booking, dishes, quantity_seed, promotions):
    order = CustomerOrder.objects.create(
        public_id=booking.public_id,
        user=user,
        booking=booking,
        order_type=CustomerOrder.TYPE_DINE_IN,
        scheduled_for=booking.start_time,
        status=CustomerOrder.STATUS_COMPLETED if booking.end_time < timezone.now() else CustomerOrder.STATUS_PENDING,
        subtotal_amount=Decimal("0.00"),
        discount_total=Decimal("0.00"),
        total_amount=Decimal("0.00"),
    )

    subtotal = Decimal("0.00")
    for offset in range(2):
        dish = dishes[(quantity_seed + offset) % len(dishes)]
        quantity = 1 + ((quantity_seed + offset) % 2)
        line_total = dish.price * quantity
        OrderItem.objects.create(
            order=order,
            dish=dish,
            dish_name_snapshot=dish.name,
            unit_price_snapshot=dish.price,
            quantity=quantity,
            line_total_snapshot=line_total,
        )
        subtotal += line_total

    discount_total = Decimal("0.00")
    if promotions:
        promo = promotions[quantity_seed % len(promotions)]
        if promo.discount_type == Promotion.DISCOUNT_PERCENT:
            discount_total = (subtotal * promo.discount_value / Decimal("100")).quantize(Decimal("0.01"))
        else:
            discount_total = min(subtotal, promo.discount_value)
        OrderAppliedPromotion.objects.create(
            order=order,
            promotion=promo,
            promotion_name_snapshot=promo.name,
            discount_amount_snapshot=discount_total,
        )

    order.subtotal_amount = subtotal
    order.discount_total = discount_total
    order.total_amount = subtotal - discount_total
    order.save(update_fields=["subtotal_amount", "discount_total", "total_amount"])
    return order


def _create_takeout_order(user, scheduled_for, dishes, quantity_seed, promotions):
    order = CustomerOrder.objects.create(
        user=user,
        order_type=CustomerOrder.TYPE_TAKEOUT,
        scheduled_for=scheduled_for,
        status=CustomerOrder.STATUS_COMPLETED if scheduled_for < timezone.now() else CustomerOrder.STATUS_PENDING,
        subtotal_amount=Decimal("0.00"),
        discount_total=Decimal("0.00"),
        total_amount=Decimal("0.00"),
    )
    subtotal = Decimal("0.00")
    for offset in range(2):
        dish = dishes[(quantity_seed + offset) % len(dishes)]
        quantity = 1 + ((quantity_seed + offset + 1) % 2)
        line_total = dish.price * quantity
        OrderItem.objects.create(
            order=order,
            dish=dish,
            dish_name_snapshot=dish.name,
            unit_price_snapshot=dish.price,
            quantity=quantity,
            line_total_snapshot=line_total,
        )
        subtotal += line_total

    discount_total = Decimal("0.00")
    if promotions:
        promo = promotions[quantity_seed % len(promotions)]
        if promo.discount_type == Promotion.DISCOUNT_PERCENT:
            discount_total = (subtotal * promo.discount_value / Decimal("100")).quantize(Decimal("0.01"))
        else:
            discount_total = min(subtotal, promo.discount_value)
        OrderAppliedPromotion.objects.create(
            order=order,
            promotion=promo,
            promotion_name_snapshot=promo.name,
            discount_amount_snapshot=discount_total,
        )

    order.subtotal_amount = subtotal
    order.discount_total = discount_total
    order.total_amount = subtotal - discount_total
    order.save(update_fields=["subtotal_amount", "discount_total", "total_amount"])
    return order


def seed_demo_data() -> dict[str, int]:
    tables, dishes = _ensure_tables_and_dishes()
    client_users, _operator, _admin = _make_users()
    clients_only = client_users[:MIN_ROWS]

    day_settings = []
    for dow in range(7):
        day_setting, _ = WeeklyMenuDaySettings.objects.get_or_create(
            day_of_week=dow,
            defaults={"is_active": dow < 5},
        )
        day_settings.append(day_setting)

    weekly_items = []
    for order in range(MIN_ROWS):
        day_setting = day_settings[order % 7]
        dish = dishes[(order // 7) % len(dishes)]
        weekly_items.append(WeeklyMenuItem(day_settings=day_setting, dish=dish, order=order))
    WeeklyMenuItem.objects.bulk_create(weekly_items)

    today = timezone.localdate()
    overrides = []
    items_per_override = min(3, len(dishes))
    override_count = max(3, math.ceil(MIN_ROWS / items_per_override))
    for idx in range(override_count):
        overrides.append(
            MenuOverride(
                date_from=today + timedelta(days=idx * 7),
                date_to=today + timedelta(days=idx * 7 + 2),
                is_active=True,
            )
        )
    MenuOverride.objects.bulk_create(overrides)

    override_items = []
    for idx, override in enumerate(MenuOverride.objects.order_by("id")):
        for jdx in range(items_per_override):
            override_items.append(
                MenuOverrideItem(
                    override=override,
                    dish=dishes[(idx + jdx) % len(dishes)],
                    action="add" if jdx % 2 == 0 else "remove",
                    order=jdx,
                )
            )
    MenuOverrideItem.objects.bulk_create(override_items)

    now = timezone.now()
    promotions = []
    for idx in range(MIN_ROWS // 2):
        promotions.append(
            Promotion(
                name=f"Dish promo {idx + 1}",
                description=f"Discount offer for dish #{idx + 1}.",
                kind=Promotion.KIND_SINGLE,
                discount_type=Promotion.DISCOUNT_PERCENT,
                discount_value=Decimal("10") + idx,
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=90),
                is_active=True,
                target_dish=dishes[idx % len(dishes)],
            )
        )
    for idx in range(MIN_ROWS - len(promotions)):
        promotions.append(
            Promotion(
                name=f"Combo promo {idx + 1}",
                description=f"Combo discount #{idx + 1}.",
                kind=Promotion.KIND_COMBO,
                discount_type=Promotion.DISCOUNT_FIXED_OFF,
                discount_value=Decimal("50") + idx * 5,
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=60),
                is_active=True,
            )
        )
    Promotion.objects.bulk_create(promotions)

    combo_promotions = list(Promotion.objects.filter(kind=Promotion.KIND_COMBO))
    combo_items = []
    combo_size = min(2, len(dishes))
    for promo in combo_promotions:
        for idx in range(combo_size):
            combo_items.append(
                PromotionComboItem(
                    promotion=promo,
                    dish=dishes[idx],
                    min_quantity=1 + idx,
                )
            )
    PromotionComboItem.objects.bulk_create(combo_items)

    single_promotions = list(Promotion.objects.filter(kind=Promotion.KIND_SINGLE))

    bookings = []
    for idx in range(MIN_ROWS):
        table, start, end, guests = _booking_slot_past(idx, tables)
        bookings.append(
            Booking(
                user=clients_only[idx % len(clients_only)],
                table=table,
                guests_count=guests,
                start_time=start,
                end_time=end,
                status=Booking.STATUS_COMPLETED,
            )
        )

    future_dates = _future_dates_within_two_working_days(max(MIN_ROWS, 5))
    if not future_dates:
        future_dates = [timezone.localdate() + timedelta(days=1)]

    for idx in range(MIN_ROWS):
        table, start, end, guests = _booking_slot_future(idx, tables, future_dates)
        bookings.append(
            Booking(
                user=clients_only[idx % len(clients_only)],
                table=table,
                guests_count=guests,
                start_time=start,
                end_time=end,
                status=Booking.STATUS_SCHEDULED,
            )
        )

    for booking in bookings:
        booking.save()

    persisted_bookings = list(Booking.objects.order_by("start_time", "pk"))
    for idx, booking in enumerate(persisted_bookings):
        promotions_for_order = single_promotions[:1] if booking.start_time >= timezone.now() and single_promotions else []
        _create_order_for_booking(booking.user, booking, dishes, idx, promotions_for_order)

    for idx in range(MIN_ROWS):
        scheduled_for = timezone.make_aware(datetime.combine(future_dates[idx % len(future_dates)], time(11 + (idx % 5), 30)))
        _create_takeout_order(
            clients_only[idx % len(clients_only)],
            scheduled_for,
            dishes,
            idx + len(persisted_bookings),
            single_promotions[:1],
        )

    past_orders = list(
        CustomerOrder.objects.filter(
            scheduled_for__lt=timezone.now(),
            order_type=CustomerOrder.TYPE_DINE_IN,
        ).prefetch_related("items__dish")[:MIN_ROWS]
    )
    for idx, order in enumerate(past_orders):
        line = order.items.first()
        if line is None:
            continue
        OrderItemReview.objects.create(
            order_item=line,
            rating=3 + (idx % 3),
            comment=f"Demo review {idx + 1}",
        )

    news_rows = []
    for idx in range(MIN_ROWS):
        published_at = timezone.now() - timedelta(days=MIN_ROWS - idx)
        news_rows.append(
            News(
                title=f"News item #{idx + 1}",
                summary=f"Summary for update #{idx + 1}.",
                body="Demo content for the cafeteria feed.",
                published_at=published_at,
                is_published=True,
            )
        )
    News.objects.bulk_create(news_rows)

    complaints = []
    for idx in range(MIN_ROWS):
        complaints.append(
            VenueComplaint(
                user=clients_only[idx % len(clients_only)],
                subject=f"Complaint #{idx + 1}",
                message=f"Demo complaint text #{idx + 1}.",
                status=["new", "seen", "closed"][idx % 3],
            )
        )
    VenueComplaint.objects.bulk_create(complaints)

    return {
        "Table": Table.objects.count(),
        "Dish": Dish.objects.count(),
        "User": User.objects.count(),
        "UserProfile": UserProfile.objects.count(),
        "Booking": Booking.objects.count(),
        "CustomerOrder": CustomerOrder.objects.count(),
        "OrderItem": OrderItem.objects.count(),
        "OrderItemReview": OrderItemReview.objects.count(),
        "WeeklyMenuDaySettings": WeeklyMenuDaySettings.objects.count(),
        "WeeklyMenuItem": WeeklyMenuItem.objects.count(),
        "MenuOverride": MenuOverride.objects.count(),
        "MenuOverrideItem": MenuOverrideItem.objects.count(),
        "News": News.objects.count(),
        "VenueComplaint": VenueComplaint.objects.count(),
        "Promotion": Promotion.objects.count(),
        "PromotionComboItem": PromotionComboItem.objects.count(),
        "OrderAppliedPromotion": OrderAppliedPromotion.objects.count(),
    }


@transaction.atomic
def run_reseed() -> dict[str, int]:
    clear_all_except_table_dish()
    return seed_demo_data()
