from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from bookings.models import (
    Booking,
    CustomerOrder,
    Dish,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    UserProfile,
)
from bookings.services.availability import build_reservation_datetimes, find_available_table, is_booking_time_allowed
from bookings.services.menu import get_menu_dishes_for_date
from bookings.services.promotions import (
    available_quantity_net,
    compute_order_totals,
    dish_ids_requiring_promotion,
    normalize_promotion_quantities_input,
    resolve_promotions_for_checkout_input,
)


def booking_detail_queryset():
    return Booking.objects.select_related(
        "table",
        "user",
        "order",
    ).prefetch_related(
        "order__applied_promotions__promotion",
        "order__items__dish",
    )


def order_detail_queryset():
    return CustomerOrder.objects.select_related(
        "booking",
        "booking__table",
        "user",
    ).prefetch_related(
        "applied_promotions__promotion",
        "items__dish",
    )


def ensure_client_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "client"})
    return profile


def is_order_completed_for_review(order):
    if isinstance(order, CustomerOrder):
        if order.booking_id:
            return order.booking.end_time < timezone.now()
        return order.scheduled_for < timezone.now()
    if isinstance(order, Booking):
        return order.end_time < timezone.now()
    return False


def get_public_id(obj):
    return getattr(obj, "public_id", None) or obj.pk


def _booking_instance(instance):
    if instance is None:
        return None
    if isinstance(instance, Booking):
        return instance
    if isinstance(instance, CustomerOrder):
        return instance.booking
    return None


def _order_instance(instance):
    if instance is None:
        return None
    if isinstance(instance, CustomerOrder):
        return instance
    if isinstance(instance, Booking) and hasattr(instance, "order"):
        return instance.order
    return None


def _normalize_dishes_payload(dishes_payload):
    dish_qty_map = {}
    for item in dishes_payload or []:
        dish_id = int(item["dish"])
        quantity = int(item["quantity"])
        if quantity > 0:
            dish_qty_map[dish_id] = dish_qty_map.get(dish_id, 0) + quantity
    return dish_qty_map


def _validate_stock(dish_qty_map, exclude_order=None):
    errors = []
    for dish in Dish.objects.filter(pk__in=list(dish_qty_map.keys())):
        requested = dish_qty_map.get(dish.pk, 0)
        available = available_quantity_net(dish, exclude_order=exclude_order)
        if requested > available:
            errors.append({dish.pk: f'Недостаточно блюда "{dish.name}". Доступно: {available}.'})
    if errors:
        raise ValidationError({"dishes": errors})


def _booking_status(start_time, end_time):
    now = timezone.now()
    if end_time < now:
        return Booking.STATUS_COMPLETED
    if start_time <= now <= end_time:
        return Booking.STATUS_SCHEDULED
    return Booking.STATUS_SCHEDULED


def _order_status(start_time, end_time):
    now = timezone.now()
    if end_time < now:
        return CustomerOrder.STATUS_COMPLETED
    return CustomerOrder.STATUS_PENDING


def _replace_order_items(order, dish_qty_map):
    existing_items = {
        item.dish_id: item
        for item in order.items.select_related("dish").all()
    }
    active_ids = []
    dishes_by_id = {dish.pk: dish for dish in Dish.objects.filter(pk__in=list(dish_qty_map.keys()))}

    for dish_id, quantity in dish_qty_map.items():
        dish = dishes_by_id[dish_id]
        unit_price = dish.price if dish else Decimal("0.00")
        line_total = unit_price * quantity
        item = existing_items.get(dish_id)
        if item is None:
            item = OrderItem(
                order=order,
                dish=dish,
                dish_name_snapshot=dish.name if dish else "",
                unit_price_snapshot=unit_price,
                quantity=quantity,
                line_total_snapshot=line_total,
            )
            item.save()
        else:
            item.dish = dish
            item.dish_name_snapshot = dish.name if dish else ""
            item.unit_price_snapshot = unit_price
            item.quantity = quantity
            item.line_total_snapshot = line_total
            item.save(update_fields=["dish", "dish_name_snapshot", "unit_price_snapshot", "quantity", "line_total_snapshot", "updated_at"])
        active_ids.append(item.pk)

    order.items.exclude(pk__in=active_ids).delete()


def _replace_applied_promotions(order, promotions, per_promo):
    existing_rows = {row.promotion_id: row for row in order.applied_promotions.all()}
    active_ids = []
    for row_data in per_promo:
        promotion = row_data["promotion"]
        row = existing_rows.get(promotion.pk)
        if row is None:
            row = OrderAppliedPromotion(
                order=order,
                promotion=promotion,
                promotion_name_snapshot=promotion.name,
                quantity_applied=row_data["quantity"],
                original_amount_snapshot=row_data["original_amount"],
                discount_amount_snapshot=row_data["discount_amount"],
            )
            row.save()
        else:
            row.promotion = promotion
            row.promotion_name_snapshot = promotion.name
            row.quantity_applied = row_data["quantity"]
            row.original_amount_snapshot = row_data["original_amount"]
            row.discount_amount_snapshot = row_data["discount_amount"]
            row.save(
                update_fields=[
                    "promotion",
                    "promotion_name_snapshot",
                    "quantity_applied",
                    "original_amount_snapshot",
                    "discount_amount_snapshot",
                ]
            )
        active_ids.append(row.pk)
    order.applied_promotions.exclude(pk__in=active_ids).delete()


def get_booking_or_404_for_user(user, public_id):
    queryset = booking_detail_queryset().filter(user=user)
    return queryset.filter(public_id=public_id).first() or queryset.filter(pk=public_id).first()


def get_order_or_404_for_user(user, public_id):
    queryset = order_detail_queryset().filter(user=user)
    return queryset.filter(public_id=public_id).first() or queryset.filter(pk=public_id).first()


@transaction.atomic
def create_or_update_reservation_for_client(*, user, data, instance=None):
    takeout = bool(data.get("takeout"))
    target_date = data["date"]
    dishes_payload = data.get("dishes", [])
    promotion_quantities = normalize_promotion_quantities_input(data)
    dish_qty_map = _normalize_dishes_payload(dishes_payload)
    menu_ids = get_menu_dishes_for_date(target_date)
    promo_only_dish_ids = dish_ids_requiring_promotion()
    forbidden_plain_dishes = [
        dish.name
        for dish in Dish.objects.filter(pk__in=list(dish_qty_map.keys()))
        if dish_qty_map.get(dish.pk, 0) > 0 and dish.pk in promo_only_dish_ids
    ]
    if forbidden_plain_dishes:
        raise ValidationError(
            {
                "dishes": [
                    f'Блюда с активной персональной акцией можно заказать только через акцию: {", ".join(forbidden_plain_dishes)}.'
                ]
            }
        )

    booking = _booking_instance(instance)
    order = _order_instance(instance)
    exclude_order = order

    guests_count = 1 if takeout else int(data["guests_count"])
    for promotion_id, quantity in promotion_quantities.items():
        if quantity > guests_count:
            raise ValidationError({"promotion_quantities": [f"Каждую акцию можно выбрать не более {guests_count} раз."]})

    promotions, per_promo, discount_amount, promotion_error, merged_qty_map = resolve_promotions_for_checkout_input(
        promotion_quantities,
        dish_qty_map,
        menu_ids,
        exclude_order=exclude_order,
    )
    if promotion_error:
        raise ValidationError({"promotion_quantities": [promotion_error]})
    if sum(merged_qty_map.values()) <= 0:
        raise ValidationError({"dishes": ["Добавьте хотя бы одно блюдо в заказ."]})

    _validate_stock(merged_qty_map, exclude_order=exclude_order)

    start_datetime, end_datetime = build_reservation_datetimes(
        target_date,
        time_str=data.get("time"),
        duration_minutes=data.get("duration_minutes"),
        takeout=takeout,
    )
    if not takeout and not is_booking_time_allowed(start_datetime):
        raise ValidationError({"time": ["Бронирование доступно минимум за 30 минут до выбранного слота по московскому времени."]})

    table = None
    if not takeout:
        table = find_available_table(
            guests_count,
            start_datetime,
            end_datetime,
            exclude_booking_id=booking.pk if booking else None,
        )
        if not table:
            raise ValidationError(
                {"time": ["На выбранное время нет свободных столиков подходящего размера."]}
            )

    dishes_by_id = {dish.pk: dish for dish in Dish.objects.filter(pk__in=list(merged_qty_map.keys()))}
    subtotal_amt, order_total_amt = compute_order_totals(merged_qty_map, dishes_by_id, discount_amount)

    if booking and not booking.can_modify_or_cancel():
        raise ValidationError({"detail": ["Нельзя изменить бронирование менее чем за 30 минут до начала."]})
    if order and not booking and not order.can_modify_or_cancel():
        raise ValidationError({"detail": ["Нельзя изменить заказ менее чем за 30 минут до начала."]})

    if not takeout:
        booking = booking or Booking(user=user, table=table, guests_count=guests_count, start_time=start_datetime, end_time=end_datetime)
        booking.user = user
        booking.table = table
        booking.guests_count = guests_count
        booking.start_time = start_datetime
        booking.end_time = end_datetime
        booking.status = _booking_status(start_datetime, end_datetime)
        booking.save()

    order = order or CustomerOrder(user=user)
    order.user = user
    order.booking = booking
    order.order_type = CustomerOrder.TYPE_TAKEOUT if takeout else CustomerOrder.TYPE_DINE_IN
    order.scheduled_for = start_datetime
    order.status = _order_status(start_datetime, end_datetime)
    order.subtotal_amount = subtotal_amt
    order.discount_total = discount_amount or Decimal("0.00")
    order.total_amount = order_total_amt
    if not takeout and booking is not None:
        order.public_id = booking.public_id or booking.pk
    order.save()

    _replace_order_items(order, merged_qty_map)
    _replace_applied_promotions(order, promotions, per_promo)

    return booking or order


@transaction.atomic
def cancel_reservation_for_client(reservation_or_booking):
    booking = _booking_instance(reservation_or_booking)
    if booking is None:
        raise ValidationError({"detail": ["Reservation not found."]})
    if not booking.can_modify_or_cancel():
        raise ValidationError({"detail": ["Нельзя отменить бронирование менее чем за 30 минут до начала."]})
    booking.delete()


@transaction.atomic
def create_dish_review(*, user, order, order_item, rating, comment):
    if not is_order_completed_for_review(order):
        raise ValidationError({"reservation_dish": ["Отзыв можно оставить только после завершения заказа."]})
    if order_item.order_id != order.id:
        raise ValidationError({"reservation_dish": ["Позиция заказа не найдена."]})
    if order.user_id != user.id:
        raise ValidationError({"reservation_dish": ["Нельзя оставить отзыв на чужой заказ."]})
    if OrderItemReview.objects.filter(order_item=order_item).exists():
        raise ValidationError({"reservation_dish": ["Отзыв по этой позиции уже оставлен."]})

    return OrderItemReview.objects.create(
        order_item=order_item,
        rating=rating,
        comment=comment or "",
    )
