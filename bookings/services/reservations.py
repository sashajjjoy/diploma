from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from bookings.models import (
    Dish,
    DishReview,
    Reservation,
    ReservationAppliedPromotion,
    ReservationDish,
    UserProfile,
)
from bookings.services.availability import build_reservation_datetimes, find_available_table
from bookings.services.menu import get_menu_dishes_for_date
from bookings.services.promotions import (
    available_quantity_net,
    compute_order_totals,
    resolve_promotions_for_checkout_input,
)


def reservation_detail_queryset():
    return Reservation.objects.select_related("applied_promotion", "table", "user").prefetch_related(
        "applied_promotion_links__promotion",
        "dishes__dish",
    )


def user_reservations_queryset(user):
    return reservation_detail_queryset().filter(user=user).order_by("-start_time")


def ensure_client_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": "client"})
    return profile


def is_order_completed_for_review(reservation):
    return reservation.end_time < timezone.now()


def _normalize_dishes_payload(dishes_payload):
    dish_qty_map = {}
    for item in dishes_payload or []:
        dish_id = int(item["dish"])
        quantity = int(item["quantity"])
        if quantity > 0:
            dish_qty_map[dish_id] = dish_qty_map.get(dish_id, 0) + quantity
    return dish_qty_map


def _replace_reservation_lines(reservation, dish_qty_map):
    ReservationDish.objects.filter(reservation=reservation).delete()
    lines = []
    for dish_id, quantity in dish_qty_map.items():
        lines.append(
            ReservationDish.objects.create(
                reservation=reservation,
                dish_id=dish_id,
                quantity=quantity,
            )
        )
    return lines


def _replace_applied_promotions(reservation, promotions, per_promo):
    reservation.applied_promotion_links.all().delete()
    for promotion, amount in per_promo:
        ReservationAppliedPromotion.objects.create(
            reservation=reservation,
            promotion=promotion,
            discount_amount=amount,
        )
    reservation.applied_promotion = promotions[0] if promotions else None


def _validate_stock(dish_qty_map, exclude_reservation=None):
    errors = []
    for dish in Dish.objects.filter(pk__in=list(dish_qty_map.keys())):
        requested = dish_qty_map.get(dish.pk, 0)
        available = available_quantity_net(dish, exclude_reservation=exclude_reservation)
        if requested > available:
            errors.append({dish.pk: f'Недостаточно блюда "{dish.name}". Доступно: {available}.'})
    if errors:
        raise ValidationError({"dishes": errors})


@transaction.atomic
def create_or_update_reservation_for_client(*, user, data, instance=None):
    takeout = bool(data.get("takeout"))
    target_date = data["date"]
    dishes_payload = data.get("dishes", [])
    promotion_ids = data.get("promotion_ids", [])
    dish_qty_map = _normalize_dishes_payload(dishes_payload)
    menu_ids = get_menu_dishes_for_date(target_date)
    exclude_reservation = instance if instance else None

    promotions, per_promo, discount_amount, promotion_error, merged_qty_map = (
        resolve_promotions_for_checkout_input(
            promotion_ids,
            dish_qty_map,
            menu_ids,
            exclude_reservation=exclude_reservation,
        )
    )
    if promotion_error:
        raise ValidationError({"promotion_ids": [promotion_error]})
    if sum(merged_qty_map.values()) <= 0:
        raise ValidationError({"dishes": ["Добавьте хотя бы одно блюдо в заказ."]})

    _validate_stock(merged_qty_map, exclude_reservation=exclude_reservation)

    start_datetime, end_datetime = build_reservation_datetimes(
        target_date,
        time_str=data.get("time"),
        duration_minutes=data.get("duration_minutes"),
        takeout=takeout,
    )

    if takeout:
        table = None
        guests_count = 1
    else:
        guests_count = int(data["guests_count"])
        table = find_available_table(
            guests_count,
            start_datetime,
            end_datetime,
            exclude_reservation_id=instance.pk if instance else None,
        )
        if not table:
            raise ValidationError(
                {
                    "time": [
                        "На выбранное время нет свободных столиков подходящего размера."
                    ]
                }
            )

    dishes_by_id = {dish.pk: dish for dish in Dish.objects.filter(pk__in=list(merged_qty_map.keys()))}
    subtotal_amt, order_total_amt = compute_order_totals(merged_qty_map, dishes_by_id, discount_amount)

    reservation = instance or Reservation(user=user)
    if instance and not instance.can_modify_or_cancel():
        raise ValidationError(
            {"detail": ["Нельзя изменить бронирование менее чем за 30 минут до начала."]}
        )

    reservation.user = user
    reservation.table = table
    reservation.guests_count = guests_count
    reservation.start_time = start_datetime
    reservation.end_time = end_datetime
    reservation.promotion_discount_total = discount_amount or Decimal("0")
    reservation.order_subtotal = subtotal_amt
    reservation.order_total = order_total_amt
    reservation.full_clean()
    reservation.save()

    _replace_reservation_lines(reservation, merged_qty_map)
    _replace_applied_promotions(reservation, promotions, per_promo)
    reservation.save(
        update_fields=[
            "applied_promotion",
            "promotion_discount_total",
            "order_subtotal",
            "order_total",
        ]
    )
    return reservation


def cancel_reservation_for_client(reservation):
    if not reservation.can_modify_or_cancel():
        raise ValidationError(
            {"detail": ["Нельзя отменить бронирование менее чем за 30 минут до начала."]}
        )
    reservation.delete()


def create_dish_review(*, user, reservation, reservation_dish, rating, comment):
    if not is_order_completed_for_review(reservation):
        raise ValidationError({"reservation_dish": ["Отзыв можно оставить только после завершения заказа."]})
    if DishReview.objects.filter(reservation_dish=reservation_dish).exists():
        raise ValidationError({"reservation_dish": ["Отзыв по этой позиции уже оставлен."]})

    review = DishReview(
        reservation_dish=reservation_dish,
        user=user,
        dish=reservation_dish.dish,
        rating=rating,
        comment=comment or "",
    )
    review.full_clean()
    review.save()
    return review
