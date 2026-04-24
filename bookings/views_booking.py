from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Avg, Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import ReservationForm
from .models import Booking, CustomerOrder, Dish, OrderItemReview, Table, UserProfile, VenueComplaint
from .services.availability import available_slots_for_date, occupied_slots_for_table_date, parse_booking_date
from .services.menu import get_menu_dishes_for_date
from .services.promotions import parse_dish_quantities_from_post, parse_promotion_ids_from_post
from .services.promotions import parse_promotion_quantities_from_post
from .services.reservations import (
    booking_detail_queryset,
    cancel_reservation_for_client,
    create_dish_review,
    create_or_update_reservation_for_client,
    get_booking_or_404_for_user,
    get_order_or_404_for_user,
    get_public_id,
    is_order_completed_for_review,
    order_detail_queryset,
)
from .views import (
    LOW_STOCK_THRESHOLD,
    _ordered_dishes_for_ids,
    client_home_promotion_context,
    is_admin_app,
    is_client,
    is_operator_or_admin,
)


def _with_legacy_pk(objects):
    adapted = []
    for obj in objects:
        obj.pk = get_public_id(obj)
        adapted.append(obj)
    return adapted


def _available_dates(now):
    from django.utils.formats import date_format

    available_dates = []
    if now.weekday() < 5:
        available_dates.append(("today", "Сегодня", now.date()))
    tomorrow = (now + timedelta(days=1)).date()
    if tomorrow.weekday() < 5:
        available_dates.append(("tomorrow", "Завтра", tomorrow))
    day_after = (now + timedelta(days=2)).date()
    while day_after.weekday() >= 5:
        day_after = day_after + timedelta(days=1)
    working_days = 0
    check_date = now.date()
    while check_date < day_after:
        if check_date.weekday() < 5:
            working_days += 1
        check_date = check_date + timedelta(days=1)
    if working_days <= 2 and day_after.weekday() < 5:
        available_dates.append(("day_after_tomorrow", date_format(day_after, "d F"), day_after))
    return available_dates


def _target_date_from_key(date_key, now):
    if date_key == "today":
        return now.date()
    if date_key == "tomorrow":
        return (now + timedelta(days=1)).date()
    if date_key == "day_after_tomorrow":
        target_date = (now + timedelta(days=2)).date()
        while target_date.weekday() >= 5:
            target_date = target_date + timedelta(days=1)
        return target_date
    raise ValidationError({"date": ["Выберите дату бронирования."]})


def _reservation_payload_from_post(request, *, is_takeout, now):
    date_key = request.POST.get("takeout_date") if is_takeout else request.POST.get("date")
    target_date = _target_date_from_key(date_key, now)
    payload = {
        "takeout": is_takeout,
        "date": target_date,
        "promotion_ids": parse_promotion_ids_from_post(request.POST),
        "promotion_quantities": parse_promotion_quantities_from_post(request.POST),
        "dishes": [
            {"dish": dish_id, "quantity": quantity}
            for dish_id, quantity in parse_dish_quantities_from_post(request.POST).items()
        ],
    }
    if not is_takeout:
        payload["time"] = request.POST.get("time")
        payload["duration_minutes"] = int(request.POST.get("duration"))
        payload["guests_count"] = int(request.POST.get("guests_count"))
    return payload


@login_required
@user_passes_test(is_client, login_url="/")
def reservation_create(request):
    UserProfile.objects.get_or_create(user=request.user, defaults={"role": "client"})

    if request.method == "POST":
        now = timezone.localtime(timezone.now())
        is_takeout = request.POST.get("takeout") == "on"
        try:
            payload = _reservation_payload_from_post(request, is_takeout=is_takeout, now=now)
            created = create_or_update_reservation_for_client(user=request.user, data=payload)
            if isinstance(created, CustomerOrder):
                messages.success(request, "Заказ на вынос успешно создан.")
                return redirect("client_order_detail", pk=get_public_id(created))
            messages.success(request, "Бронирование успешно создано.")
            return redirect("reservation_detail", pk=get_public_id(created))
        except (ValueError, TypeError) as exc:
            messages.error(request, f"Ошибка при обработке данных: {exc}")
        except ValidationError as exc:
            message_dict = getattr(exc, "message_dict", {"detail": exc.messages})
            for errors in message_dict.values():
                for error in errors:
                    messages.error(request, error)

    form = ReservationForm()
    reservations = _with_legacy_pk(list(booking_detail_queryset().filter(user=request.user).order_by("-start_time")[:10]))
    now = timezone.localtime(timezone.now())
    available_dates = _available_dates(now)
    time_slots = [f"{hour:02d}:{minute:02d}" for hour in range(12, 22) for minute in (0, 30)]
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by("name")

    import json

    dishes_by_date = {date_key: list(get_menu_dishes_for_date(date_obj)) for date_key, _, date_obj in available_dates}
    today_menu_ids = list(get_menu_dishes_for_date(now.date()))
    today_menu_dishes = _ordered_dishes_for_ids(today_menu_ids)

    return render(
        request,
        "bookings/client_home.html",
        {
            "form": form,
            "reservations": reservations,
            "available_dates": available_dates,
            "time_slots": time_slots,
            "all_dishes": all_dishes,
            "today_menu_dishes": today_menu_dishes,
            "today_menu_date": now.date(),
            "dishes_by_date": json.dumps(dishes_by_date),
            **client_home_promotion_context(),
        },
    )


@login_required
@user_passes_test(is_client, login_url="/")
def client_today_menu(request):
    today = timezone.localtime(timezone.now()).date()
    today_menu_ids = list(get_menu_dishes_for_date(today))
    today_menu_dishes = _ordered_dishes_for_ids(today_menu_ids)
    return render(
        request,
        "bookings/client_today_menu.html",
        {
            "today_menu_date": today,
            "today_menu_dishes": today_menu_dishes,
        },
    )


@login_required
@user_passes_test(is_client, login_url="/")
def reservation_detail(request, pk):
    reservation = get_booking_or_404_for_user(request.user, pk)
    if reservation is None:
        raise Http404
    return render(
        request,
        "bookings/reservation_detail.html",
        {
            "reservation": reservation,
            "dishes": reservation.dishes,
            "can_modify": reservation.can_modify_or_cancel(),
            "now": timezone.localtime(timezone.now()),
        },
    )


@login_required
@user_passes_test(is_client, login_url="/")
def reservation_edit(request, pk):
    reservation = get_booking_or_404_for_user(request.user, pk)
    if reservation is None:
        raise Http404
    if not reservation.can_modify_or_cancel():
        messages.error(request, "Невозможно изменить бронирование. До начала осталось менее 30 минут.")
        return redirect("reservation_detail", pk=get_public_id(reservation))

    if request.method == "POST":
        now = timezone.localtime(timezone.now())
        try:
            payload = _reservation_payload_from_post(request, is_takeout=False, now=now)
            updated = create_or_update_reservation_for_client(user=request.user, data=payload, instance=reservation)
            messages.success(request, "Бронирование успешно изменено.")
            return redirect("reservation_detail", pk=get_public_id(updated))
        except (ValueError, TypeError) as exc:
            messages.error(request, f"Ошибка при обработке данных: {exc}")
        except ValidationError as exc:
            message_dict = getattr(exc, "message_dict", {"detail": exc.messages})
            for errors in message_dict.values():
                for error in errors:
                    messages.error(request, error)

    now = timezone.localtime(timezone.now())
    available_dates = _available_dates(now)
    time_slots = [f"{hour:02d}:{minute:02d}" for hour in range(12, 22) for minute in (0, 30)]
    reservation_date = timezone.localtime(reservation.start_time).date()
    reservation_duration_minutes = int((reservation.end_time - reservation.start_time).total_seconds() / 60)
    reservation_duration = 25 if reservation_duration_minutes <= 40 else 55

    selected_date_key = next(
        (date_key for date_key, _, date_obj in available_dates if date_obj == reservation_date),
        available_dates[0][0] if available_dates else "today",
    )

    import json

    dishes_by_date = {date_key: list(get_menu_dishes_for_date(date_obj)) for date_key, _, date_obj in available_dates}

    return render(
        request,
        "bookings/reservation_edit.html",
        {
            "reservation": reservation,
            "available_dates": available_dates,
            "time_slots": time_slots,
            "selected_date_key": selected_date_key,
            "selected_time": reservation.start_time.strftime("%H:%M"),
            "selected_duration": reservation_duration,
            "selected_guests_count": reservation.guests_count,
            "all_dishes": Dish.objects.filter(available_quantity__gt=0).order_by("name"),
            "current_dishes": {item.dish_id: item.quantity for item in reservation.dishes},
            "menu_dishes_for_date": list(get_menu_dishes_for_date(reservation_date)),
            "dishes_by_date": json.dumps(dishes_by_date),
        },
    )


@login_required
@user_passes_test(is_client, login_url="/")
def reservation_delete(request, pk):
    reservation = get_booking_or_404_for_user(request.user, pk)
    if reservation is None:
        raise Http404
    if not reservation.can_modify_or_cancel():
        messages.error(request, "Невозможно отменить бронирование. До начала осталось менее 30 минут.")
        return redirect("reservation_detail", pk=get_public_id(reservation))
    if request.method == "POST":
        cancel_reservation_for_client(reservation)
        messages.success(request, "Бронирование успешно отменено.")
        return redirect("home")
    return render(request, "bookings/reservation_confirm_delete.html", {"reservation": reservation})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_cabinet(request):
    now = timezone.now()
    today_date = timezone.localtime(now).date()
    week_ago = now - timedelta(days=7)
    recent_reservations = booking_detail_queryset().order_by("-created_at")[:10]
    recent_reviews = OrderItemReview.objects.select_related("order_item__order__user", "order_item__dish").order_by("-created_at")[:8]
    return render(
        request,
        "bookings/admin_cabinet.html",
        {
            "reservations_today": Booking.objects.filter(start_time__date=today_date).count(),
            "reservations_active": Booking.objects.exclude(status=Booking.STATUS_CANCELLED).filter(start_time__lte=now, end_time__gte=now).count(),
            "reservations_completed_week": Booking.objects.filter(end_time__lt=now, end_time__gte=week_ago).count(),
            "complaints_new": VenueComplaint.objects.filter(status="new").count(),
            "reviews_total": OrderItemReview.objects.count(),
            "dishes_low_stock": list(Dish.objects.filter(available_quantity__lt=LOW_STOCK_THRESHOLD).order_by("available_quantity", "name")[:12]),
            "users_by_role": list(UserProfile.objects.values("role").annotate(n=Count("id")).order_by("role")),
            "recent_reservations": recent_reservations,
            "recent_complaints": VenueComplaint.objects.select_related("user").order_by("-created_at")[:8],
            "recent_reviews": recent_reviews,
            "low_stock_threshold": LOW_STOCK_THRESHOLD,
        },
    )


@login_required
@user_passes_test(is_operator_or_admin, login_url="/")
def operator_cabinet(request):
    return render(
        request,
        "bookings/operator_cabinet.html",
        {
            "reservations": booking_detail_queryset().order_by("-start_time")[:20],
            "tables": Table.objects.all().order_by("table_number"),
            "dishes": Dish.objects.all().order_by("name"),
        },
    )


@login_required
@user_passes_test(is_operator_or_admin, login_url="/")
def operator_reservations(request):
    reservations = booking_detail_queryset().order_by("-start_time")
    if request.GET.get("table"):
        reservations = reservations.filter(table_id=request.GET["table"])
    if request.GET.get("client"):
        reservations = reservations.filter(user_id=request.GET["client"])
    if request.GET.get("date_from"):
        reservations = reservations.filter(start_time__gte=request.GET["date_from"])
    if request.GET.get("date_to"):
        reservations = reservations.filter(start_time__lte=request.GET["date_to"])
    paginator = Paginator(reservations, 10)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    from django.contrib.auth.models import User

    return render(
        request,
        "bookings/operator_reservations.html",
        {
            "reservations": page_obj,
            "tables": Table.objects.all().order_by("table_number"),
            "clients": User.objects.filter(profile__role="client").order_by("first_name", "last_name", "username"),
            "page_obj": page_obj,
        },
    )


@login_required
@user_passes_test(is_operator_or_admin, login_url="/")
def operator_reservation_detail(request, pk):
    reservation = booking_detail_queryset().filter(public_id=pk).first()
    if reservation is None:
        reservation = get_object_or_404(booking_detail_queryset(), pk=pk)
    return render(request, "bookings/operator_reservation_detail.html", {"reservation": reservation, "dishes": reservation.dishes})


@login_required
@user_passes_test(is_operator_or_admin, login_url="/")
def operator_reservation_delete(request, pk):
    reservation = booking_detail_queryset().filter(public_id=pk).first()
    if reservation is None:
        reservation = get_object_or_404(booking_detail_queryset(), pk=pk)
    if request.method == "POST":
        reservation.delete()
        messages.success(request, "Бронирование успешно удалено.")
        return redirect("operator_reservations")
    return render(request, "bookings/operator_reservation_confirm_delete.html", {"reservation": reservation})


@login_required
@user_passes_test(is_client, login_url="/")
def client_order_list(request):
    orders = order_detail_queryset().filter(user=request.user).order_by("-scheduled_for")
    return render(request, "bookings/client_order_list.html", {"orders": orders})


@login_required
@user_passes_test(is_client, login_url="/")
def client_order_detail(request, pk):
    order = get_order_or_404_for_user(request.user, pk)
    if order is None:
        raise Http404
    completed = is_order_completed_for_review(order)
    reviewed_line_ids = set(order.items.filter(review__isnull=False).values_list("pk", flat=True))
    line_info = [{"line": line, "can_review": completed and line.pk not in reviewed_line_ids} for line in order.items.select_related("dish")]
    return render(
        request,
        "bookings/client_order_detail.html",
        {"reservation": order, "line_info": line_info, "completed": completed},
    )


@login_required
@user_passes_test(is_client, login_url="/")
def client_dish_review_list(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    reviews = OrderItemReview.objects.filter(order_item__dish=dish).select_related("order_item__order__user").order_by("-created_at")
    review_stats = reviews.aggregate(avg_rating=Avg("rating"), reviews_count=Count("id"))
    eligible_reviews = []
    if request.user.is_authenticated:
        candidate_items = (
            order_detail_queryset()
            .filter(user=request.user, items__dish=dish)
            .prefetch_related("items__dish", "items__review")
            .distinct()
        )
        for order in candidate_items:
            if not is_order_completed_for_review(order):
                continue
            for line in order.items.all():
                if line.dish_id == dish.pk and not hasattr(line, "review"):
                    eligible_reviews.append({"order": order, "line": line})
    return render(
        request,
        "bookings/client_dish_review_list.html",
        {
            "dish": dish,
            "reviews": reviews,
            "avg_rating": review_stats["avg_rating"],
            "reviews_count": review_stats["reviews_count"],
            "eligible_reviews": eligible_reviews,
        },
    )


@login_required
@user_passes_test(is_client, login_url="/")
def client_dish_review_create(request, rid, line_id):
    order = get_order_or_404_for_user(request.user, rid)
    if order is None:
        raise Http404
    line = order.items.filter(public_id=line_id).first() or get_object_or_404(order.items.all(), pk=line_id)
    if not is_order_completed_for_review(order):
        messages.error(request, "Отзыв можно оставить только после завершения заказа.")
        return redirect("client_order_detail", pk=get_public_id(order))
    if OrderItemReview.objects.filter(order_item=line).exists():
        messages.info(request, "Отзыв по этой позиции уже оставлен.")
        return redirect("client_order_detail", pk=get_public_id(order))
    if request.method == "POST":
        try:
            rating = int(request.POST.get("rating", "0"))
        except ValueError:
            rating = 0
        comment = request.POST.get("comment", "").strip()
        if rating < 1 or rating > 5:
            messages.error(request, "Выберите оценку от 1 до 5.")
        else:
            try:
                create_dish_review(user=request.user, order=order, order_item=line, rating=rating, comment=comment)
                messages.success(request, "Спасибо за отзыв!")
                return redirect("client_order_detail", pk=get_public_id(order))
            except ValidationError as exc:
                messages.error(request, str(exc))
    return render(request, "bookings/client_dish_review_form.html", {"reservation": order, "line": line})


@require_http_methods(["GET"])
def get_occupied_time_slots(request):
    table_id = request.GET.get("table_id")
    date_raw = request.GET.get("date")
    if not table_id or not date_raw:
        return JsonResponse({"error": "table_id and date are required"}, status=400)
    table = get_object_or_404(Table, pk=table_id)
    try:
        target_date = parse_booking_date(date_raw)
    except ValueError:
        return JsonResponse({"error": "invalid date"}, status=400)
    occupied = occupied_slots_for_table_date(table, target_date, booking_id=request.GET.get("reservation_id"))
    return JsonResponse({"occupied_slots": occupied})


@require_http_methods(["GET"])
def check_available_time_slots(request):
    date_raw = request.GET.get("date")
    guests_count = request.GET.get("guests_count")
    if not date_raw or not guests_count:
        return JsonResponse({"error": "date and guests_count are required"}, status=400)
    try:
        target_date = parse_booking_date(date_raw)
    except ValueError:
        return JsonResponse({"error": "invalid date"}, status=400)
    return JsonResponse({"available_slots": available_slots_for_date(target_date, int(guests_count))})
