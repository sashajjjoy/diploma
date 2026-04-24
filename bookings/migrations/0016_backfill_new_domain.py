from django.db import connection, migrations
from django.utils import timezone


DEFAULT_WEEKLY_MENU_NAME = "Default weekly menu"


def add_booking_exclusion_constraint(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")
        cursor.execute(
            """
            ALTER TABLE bookings_booking
            ADD CONSTRAINT booking_no_overlap
            EXCLUDE USING gist (
                table_id WITH =,
                tstzrange(start_time, end_time) WITH &&
            );
            """
        )


def remove_booking_exclusion_constraint(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            ALTER TABLE bookings_booking
            DROP CONSTRAINT IF EXISTS booking_no_overlap;
            """
        )


def backfill_new_domain(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")
    CustomerOrder = apps.get_model("bookings", "CustomerOrder")
    DishReview = apps.get_model("bookings", "DishReview")
    MenuOverride = apps.get_model("bookings", "MenuOverride")
    OrderAppliedPromotion = apps.get_model("bookings", "OrderAppliedPromotion")
    OrderItem = apps.get_model("bookings", "OrderItem")
    OrderItemReview = apps.get_model("bookings", "OrderItemReview")
    Promotion = apps.get_model("bookings", "Promotion")
    PromotionComboItem = apps.get_model("bookings", "PromotionComboItem")
    PromotionDishRule = apps.get_model("bookings", "PromotionDishRule")
    Reservation = apps.get_model("bookings", "Reservation")
    ReservationAppliedPromotion = apps.get_model("bookings", "ReservationAppliedPromotion")
    ReservationDish = apps.get_model("bookings", "ReservationDish")
    WeeklyMenu = apps.get_model("bookings", "WeeklyMenu")
    WeeklyMenuDay = apps.get_model("bookings", "WeeklyMenuDay")
    WeeklyMenuDayItem = apps.get_model("bookings", "WeeklyMenuDayItem")
    WeeklyMenuDaySettings = apps.get_model("bookings", "WeeklyMenuDaySettings")
    WeeklyMenuItem = apps.get_model("bookings", "WeeklyMenuItem")

    weekly_menu, _ = WeeklyMenu.objects.get_or_create(
        name=DEFAULT_WEEKLY_MENU_NAME,
        defaults={"is_active": True},
    )

    for day_settings in WeeklyMenuDaySettings.objects.all().iterator():
        weekly_menu_day, _ = WeeklyMenuDay.objects.update_or_create(
            weekly_menu_id=weekly_menu.pk,
            day_of_week=day_settings.day_of_week,
            defaults={"is_active": day_settings.is_active},
        )
        synced_item_ids = []
        for item in WeeklyMenuItem.objects.filter(day_settings_id=day_settings.pk).iterator():
            projected_item, _ = WeeklyMenuDayItem.objects.update_or_create(
                weekly_menu_day_id=weekly_menu_day.pk,
                dish_id=item.dish_id,
                defaults={"sort_order": item.order},
            )
            synced_item_ids.append(projected_item.pk)
        WeeklyMenuDayItem.objects.filter(weekly_menu_day_id=weekly_menu_day.pk).exclude(pk__in=synced_item_ids).delete()

    MenuOverride.objects.filter(weekly_menu__isnull=True).update(weekly_menu_id=weekly_menu.pk)

    now = timezone.now()
    for reservation in Reservation.objects.all().iterator():
        booking = None
        if reservation.table_id is not None:
            booking_status = "completed" if reservation.end_time < now else "scheduled"
            booking, _ = Booking.objects.update_or_create(
                legacy_reservation_id=reservation.pk,
                defaults={
                    "user_id": reservation.user_id,
                    "table_id": reservation.table_id,
                    "guests_count": reservation.guests_count,
                    "start_time": reservation.start_time,
                    "end_time": reservation.end_time,
                    "status": booking_status,
                },
            )
        order_status = "completed" if reservation.end_time < now else "pending"
        CustomerOrder.objects.update_or_create(
            legacy_reservation_id=reservation.pk,
            defaults={
                "user_id": reservation.user_id,
                "booking_id": booking.pk if booking else None,
                "order_type": (
                    "dine_in" if reservation.table_id is not None else "takeout"
                ),
                "scheduled_for": reservation.start_time,
                "status": order_status,
                "subtotal_amount": reservation.order_subtotal,
                "discount_total": reservation.promotion_discount_total,
                "total_amount": reservation.order_total,
            },
        )

    for reservation_line in ReservationDish.objects.select_related("dish", "reservation").iterator():
        order = CustomerOrder.objects.get(legacy_reservation_id=reservation_line.reservation_id)
        dish_name = reservation_line.dish.name if reservation_line.dish_id else ""
        unit_price = reservation_line.dish.price if reservation_line.dish_id else 0
        OrderItem.objects.update_or_create(
            legacy_reservation_dish_id=reservation_line.pk,
            defaults={
                "order_id": order.pk,
                "dish_id": reservation_line.dish_id,
                "dish_name_snapshot": dish_name,
                "unit_price_snapshot": unit_price,
                "quantity": reservation_line.quantity,
                "line_total_snapshot": unit_price * reservation_line.quantity,
            },
        )

    for reservation in Reservation.objects.all().iterator():
        order = CustomerOrder.objects.get(legacy_reservation_id=reservation.pk)
        applied_rows = list(ReservationAppliedPromotion.objects.filter(reservation_id=reservation.pk).iterator())
        if applied_rows:
            for applied_row in applied_rows:
                OrderAppliedPromotion.objects.update_or_create(
                    legacy_applied_promotion_id=applied_row.pk,
                    defaults={
                        "order_id": order.pk,
                        "promotion_id": applied_row.promotion_id,
                        "promotion_name_snapshot": applied_row.promotion.name,
                        "discount_amount_snapshot": applied_row.discount_amount,
                    },
                )
        elif reservation.applied_promotion_id:
            OrderAppliedPromotion.objects.update_or_create(
                order_id=order.pk,
                promotion_id=reservation.applied_promotion_id,
                defaults={
                    "promotion_name_snapshot": reservation.applied_promotion.name,
                    "discount_amount_snapshot": reservation.promotion_discount_total,
                },
            )

    for promotion in Promotion.objects.all().iterator():
        PromotionDishRule.objects.filter(promotion_id=promotion.pk).delete()
        if promotion.kind == "single_dish" and promotion.target_dish_id:
            PromotionDishRule.objects.create(
                promotion_id=promotion.pk,
                dish_id=promotion.target_dish_id,
                rule_role="target",
                min_quantity=1,
                sort_order=0,
            )
        elif promotion.kind == "combo":
            combo_items = list(PromotionComboItem.objects.filter(promotion_id=promotion.pk).order_by("pk"))
            for index, combo_item in enumerate(combo_items):
                PromotionDishRule.objects.create(
                    promotion_id=promotion.pk,
                    dish_id=combo_item.dish_id,
                    rule_role="required",
                    min_quantity=combo_item.min_quantity,
                    sort_order=index,
                )

    for review in DishReview.objects.select_related("reservation_dish").iterator():
        try:
            order_item = OrderItem.objects.get(legacy_reservation_dish_id=review.reservation_dish_id)
        except OrderItem.DoesNotExist:
            continue
        OrderItemReview.objects.update_or_create(
            legacy_dish_review_id=review.pk,
            defaults={
                "order_item_id": order_item.pk,
                "rating": review.rating,
                "comment": review.comment,
            },
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0015_auto_20260422_2310"),
    ]

    operations = [
        migrations.RunPython(backfill_new_domain, noop_reverse),
        migrations.RunPython(add_booking_exclusion_constraint, remove_booking_exclusion_constraint),
    ]
