from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import models
from django.db.models import Avg, Count, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    BackupArchive,
    Booking,
    CustomerOrder,
    Dish,
    ExternalIntegration,
    LoginAttempt,
    OrderItemReview,
    ServiceDurationOption,
    ServiceWeekdayWindow,
    Table,
    UserProfile,
    VenueComplaint,
)
from .services.availability import (
    available_slots_for_date,
    build_time_slots,
    get_bookable_dates,
    get_date_label,
    get_duration_values,
    get_slot_settings,
    occupied_slots_for_table_date,
    parse_booking_date,
)
from .services.backup import create_backup_archive, restore_backup_archive
from .services.integrations import check_external_integration
from .services.menu import get_menu_dishes_for_date
from .services.promotions import parse_dish_quantities_from_post, parse_promotion_ids_from_post, parse_promotion_quantities_from_post
from .services.reports import admin_report_rows, csv_response, operator_report_rows, parse_report_period
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
from .services.security import get_security_settings, unlock_login_attempt
from .views import LOW_STOCK_THRESHOLD, _ordered_dishes_for_ids, client_home_promotion_context, is_admin_app, is_client, is_operator_app


def _with_legacy_pk(objects):
    adapted = []
    for obj in objects:
        obj.pk = get_public_id(obj)
        adapted.append(obj)
    return adapted


def _ensure_profiles():
    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.ROLE_CLIENT})


def _available_dates(now):
    return [(item.isoformat(), get_date_label(item, now.date()), item) for item in get_bookable_dates(now=now)]


def _client_time_slots(available_dates, duration_options):
    slot_union = []
    basis_durations = duration_options[:1] or [0]
    for _, _, date_obj in available_dates:
        for duration in basis_durations:
            for slot in build_time_slots(date_obj, duration_minutes=duration):
                if slot not in slot_union:
                    slot_union.append(slot)
    return slot_union


def _target_date_from_key(date_key):
    try:
        return parse_booking_date(date_key)
    except ValueError:
        raise ValidationError({"date": ["Выберите дату бронирования."]})


def _reservation_payload_from_post(request, *, is_takeout):
    date_key = request.POST.get("takeout_date") if is_takeout else request.POST.get("date")
    payload = {
        "takeout": is_takeout,
        "date": _target_date_from_key(date_key),
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


def _role_summary():
    return list(UserProfile.objects.values("role").annotate(n=Count("id")).order_by("role"))


def _active_admin_users():
    return User.objects.filter(is_active=True).filter(
        models.Q(is_superuser=True) | models.Q(profile__role=UserProfile.ROLE_ADMIN)
    )


def _client_form_context(*, now, reservations=None):
    available_dates = _available_dates(now)
    duration_options = get_duration_values()
    time_slots = _client_time_slots(available_dates, duration_options)
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by("name")

    import json

    today_menu_ids = list(get_menu_dishes_for_date(now.date()))
    today_menu_dishes = _ordered_dishes_for_ids(today_menu_ids)
    # On the client main page show only dishes available today.
    dishes_by_date = {date_key: today_menu_ids for date_key, _, _ in available_dates}
    return {        "reservations": reservations or [],
        "available_dates": available_dates,
        "time_slots": time_slots,
        "duration_options": duration_options,
        "all_dishes": today_menu_dishes,
        "today_menu_dishes": today_menu_dishes,
        "today_menu_date": now.date(),
        "dishes_by_date": json.dumps(dishes_by_date),
        **client_home_promotion_context(),
    }


def _operator_dashboard_context():
    now = timezone.now()
    date_from = timezone.localdate() - timedelta(days=30)
    bookings_period = Booking.objects.filter(start_time__date__gte=date_from, start_time__date__lte=timezone.localdate())
    complaints_period = VenueComplaint.objects.filter(created_at__date__gte=date_from, created_at__date__lte=timezone.localdate())
    reviews_period = OrderItemReview.objects.filter(created_at__date__gte=date_from, created_at__date__lte=timezone.localdate())
    sales_period = (
        order_detail_queryset()
        .filter(scheduled_for__date__gte=date_from, scheduled_for__date__lte=timezone.localdate())
        .values("items__dish_name_snapshot")
        .annotate(
            total_quantity=Sum("items__quantity"),
            total_revenue=Sum("items__line_total_snapshot"),
        )
        .exclude(items__dish_name_snapshot__isnull=True)
    )
    top_dishes = list(sales_period.order_by("-total_quantity", "items__dish_name_snapshot")[:5])
    low_dishes = list(sales_period.order_by("total_quantity", "items__dish_name_snapshot")[:5])
    return {
        "reservations_today": Booking.objects.filter(start_time__date=timezone.localdate()).count(),
        "reservations_active": Booking.objects.exclude(status=Booking.STATUS_CANCELLED).filter(start_time__lte=now, end_time__gte=now).count(),
        "reservations_completed_period": bookings_period.filter(status=Booking.STATUS_COMPLETED).count(),
        "reservations_cancelled_period": bookings_period.filter(status=Booking.STATUS_CANCELLED).count(),
        "complaints_new": complaints_period.filter(status="new").count(),
        "complaints_seen": complaints_period.filter(status="seen").count(),
        "complaints_closed": complaints_period.filter(status="closed").count(),
        "reviews_total": reviews_period.count(),
        "reviews_avg": reviews_period.aggregate(avg=Avg("rating"))["avg"],
        "reviews_new_period": reviews_period.count(),
        "sales_total_revenue": sum((row["total_revenue"] or 0) for row in sales_period),
        "top_dishes": top_dishes,
        "low_dishes": low_dishes,
        "low_stock_dishes": Dish.objects.filter(available_quantity__lt=LOW_STOCK_THRESHOLD).order_by("available_quantity", "name")[:8],
    }


def _validate_managed_user_update(*, acting_user, managed_user, profile):
    new_username = acting_user.POST.get("username", "").strip()
    if not new_username:
        raise ValidationError("Логин не может быть пустым.")
    if User.objects.exclude(pk=managed_user.pk).filter(username=new_username).exists():
        raise ValidationError("Пользователь с таким логином уже существует.")

    email = acting_user.POST.get("email", "").strip()
    if email:
        validate_email(email)

    allowed_roles = {choice[0] for choice in UserProfile.ROLE_CHOICES}
    new_role = acting_user.POST.get("role", profile.role)
    if new_role not in allowed_roles:
        raise ValidationError("Выбрана недопустимая роль.")

    new_is_active = acting_user.POST.get("is_active") == "on"
    currently_admin = managed_user.is_superuser or profile.role == UserProfile.ROLE_ADMIN
    future_admin = managed_user.is_superuser or new_role == UserProfile.ROLE_ADMIN
    other_active_admins = _active_admin_users().exclude(pk=managed_user.pk).count()
    if currently_admin and other_active_admins == 0 and (not new_is_active or not future_admin):
        raise ValidationError("Нельзя отключить или разжаловать последнего активного администратора.")

    if managed_user.pk == acting_user.user.pk and not future_admin:
        raise ValidationError("Администратор не может снять административную роль у самого себя.")

    return {
        "username": new_username,
        "first_name": acting_user.POST.get("first_name", "").strip(),
        "last_name": acting_user.POST.get("last_name", "").strip(),
        "email": email,
        "phone": acting_user.POST.get("phone", "").strip(),
        "role": new_role,
        "is_active": new_is_active,
    }


@login_required
@user_passes_test(is_client, login_url="/")
def reservation_create(request):
    UserProfile.objects.get_or_create(user=request.user, defaults={"role": UserProfile.ROLE_CLIENT})

    if request.method == "POST":
        is_takeout = request.POST.get("takeout") == "on"
        try:
            payload = _reservation_payload_from_post(request, is_takeout=is_takeout)
            created = create_or_update_reservation_for_client(user=request.user, data=payload)
            if isinstance(created, CustomerOrder):
                messages.success(request, "Заказ на вынос успешно создан.")
                return redirect("client_order_detail", pk=get_public_id(created))
            messages.success(request, "Бронирование успешно создано.")
            return redirect("reservation_detail", pk=get_public_id(created))
        except (ValueError, TypeError) as exc:
            messages.error(request, f"РћС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ РґР°РЅРЅС‹С…: {exc}")
        except ValidationError as exc:
            message_dict = getattr(exc, "message_dict", {"detail": exc.messages})
            for errors in message_dict.values():
                for error in errors:
                    messages.error(request, error)

    reservations = _with_legacy_pk(list(booking_detail_queryset().filter(user=request.user).order_by("-start_time")[:10]))
    now = timezone.localtime(timezone.now())
    return render(
        request,
        "bookings/client_home.html",
        _client_form_context(now=now, reservations=reservations),
    )


@login_required
@user_passes_test(is_client, login_url="/")
def client_today_menu(request):
    today = timezone.localtime(timezone.now()).date()
    today_menu_ids = list(get_menu_dishes_for_date(today))
    today_menu_dishes = _ordered_dishes_for_ids(today_menu_ids)
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by("name")
    return render(
        request,
        "bookings/client_today_menu.html",
        {
            "today_menu_date": today,
            "today_menu_dishes": today_menu_dishes,
            "all_dishes": all_dishes,
            "today_menu_ids": today_menu_ids,
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
        messages.error(request, "Невозможно изменить бронирование. До начала осталось меньше допустимого времени.")
        return redirect("reservation_detail", pk=get_public_id(reservation))

    if request.method == "POST":
        try:
            payload = _reservation_payload_from_post(request, is_takeout=False)
            updated = create_or_update_reservation_for_client(user=request.user, data=payload, instance=reservation)
            messages.success(request, "Бронирование успешно изменено.")
            return redirect("reservation_detail", pk=get_public_id(updated))
        except (ValueError, TypeError) as exc:
            messages.error(request, f"РћС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ РґР°РЅРЅС‹С…: {exc}")
        except ValidationError as exc:
            message_dict = getattr(exc, "message_dict", {"detail": exc.messages})
            for errors in message_dict.values():
                for error in errors:
                    messages.error(request, error)

    now = timezone.localtime(timezone.now())
    available_dates = _available_dates(now)
    duration_options = get_duration_values()
    if reservation_duration not in duration_options:
        duration_options = sorted(duration_options + [reservation_duration])
    time_slots = _client_time_slots(available_dates, duration_options)
    reservation_date = timezone.localtime(reservation.start_time).date()
    reservation_duration = int((reservation.end_time - reservation.start_time).total_seconds() / 60)

    selected_date_key = next(
        (date_key for date_key, _, date_obj in available_dates if date_obj == reservation_date),
        reservation_date.isoformat(),
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
            "duration_options": duration_options,
            "selected_date_key": selected_date_key,
            "selected_time": timezone.localtime(reservation.start_time).strftime("%H:%M"),
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
        messages.error(request, "Невозможно отменить бронирование. До начала осталось меньше допустимого времени.")
        return redirect("reservation_detail", pk=get_public_id(reservation))
    if request.method == "POST":
        cancel_reservation_for_client(reservation)
        messages.success(request, "Бронирование успешно отменено.")
        return redirect("home")
    return render(request, "bookings/reservation_confirm_delete.html", {"reservation": reservation})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_cabinet(request):
    _ensure_profiles()
    if request.method == "POST":
        user_id = request.POST.get("user_id")
        managed_user = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
        profile, _ = UserProfile.objects.get_or_create(user=managed_user, defaults={"role": UserProfile.ROLE_CLIENT})
        try:
            cleaned = _validate_managed_user_update(acting_user=request, managed_user=managed_user, profile=profile)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("admin_cabinet")

        managed_user.username = cleaned["username"]
        managed_user.first_name = cleaned["first_name"]
        managed_user.last_name = cleaned["last_name"]
        managed_user.email = cleaned["email"]
        managed_user.is_active = cleaned["is_active"]
        managed_user.save(update_fields=["username", "first_name", "last_name", "email", "is_active"])
        profile.role = cleaned["role"]
        profile.phone = cleaned["phone"] or None
        profile.save(update_fields=["role", "phone"])
        messages.success(request, f"Р”Р°РЅРЅС‹Рµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ {managed_user.username} РѕР±РЅРѕРІР»РµРЅС‹.")
        return redirect("admin_cabinet")

    now = timezone.now()
    today_date = timezone.localtime(now).date()
    week_ago = now - timedelta(days=7)
    users = User.objects.select_related("profile").order_by("username")
    integrations_total = ExternalIntegration.objects.count()
    backups_total = BackupArchive.objects.count()
    return render(
        request,
        "bookings/admin_cabinet.html",
        {
            "reservations_today": Booking.objects.filter(start_time__date=today_date).count(),
            "reservations_active": Booking.objects.exclude(status=Booking.STATUS_CANCELLED).filter(start_time__lte=now, end_time__gte=now).count(),
            "reservations_completed_week": Booking.objects.filter(end_time__lt=now, end_time__gte=week_ago).count(),
            "total_users": users.count(),
            "clients_total": UserProfile.objects.filter(role=UserProfile.ROLE_CLIENT).count(),
            "operators_total": UserProfile.objects.filter(role=UserProfile.ROLE_OPERATOR).count(),
            "admins_total": UserProfile.objects.filter(role=UserProfile.ROLE_ADMIN).count(),
            "integrations_total": integrations_total,
            "backups_total": backups_total,
            "dishes_low_stock": list(Dish.objects.filter(available_quantity__lt=LOW_STOCK_THRESHOLD).order_by("available_quantity", "name")[:12]),
            "users_by_role": _role_summary(),
            "recent_reservations": booking_detail_queryset().order_by("-created_at")[:10],
            "managed_users": users,
            "role_choices": UserProfile.ROLE_CHOICES,
            "low_stock_threshold": LOW_STOCK_THRESHOLD,
        },
    )


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_integrations(request):
    if request.method == "POST":
        integration_id = request.POST.get("integration_id")
        integration = get_object_or_404(ExternalIntegration, pk=integration_id) if integration_id else ExternalIntegration()
        integration.name = request.POST.get("name", "").strip()
        integration.base_url = request.POST.get("base_url", "").strip()
        integration.auth_type = request.POST.get("auth_type", ExternalIntegration.AUTH_NONE)
        integration.timeout_seconds = int(request.POST.get("timeout_seconds") or 10)
        secret_token = request.POST.get("secret_token", "")
        if secret_token:
            integration.secret_token = secret_token.strip()
        integration.notes = request.POST.get("notes", "").strip()
        integration.is_active = request.POST.get("is_active") == "on"
        try:
            integration.full_clean()
            integration.save()
            messages.success(request, "РРЅС‚РµРіСЂР°С†РёСЏ СЃРѕС…СЂР°РЅРµРЅР°.")
        except ValidationError as exc:
            for errors in getattr(exc, "message_dict", {"detail": exc.messages}).values():
                for error in errors:
                    messages.error(request, error)
        return redirect("admin_integrations")

    return render(
        request,
        "bookings/admin_integrations.html",
        {"integrations": ExternalIntegration.objects.order_by("name"), "auth_choices": ExternalIntegration.AUTH_CHOICES},
    )


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_integration_delete(request, pk):
    integration = get_object_or_404(ExternalIntegration, pk=pk)
    if request.method == "POST":
        integration.delete()
        messages.success(request, "РРЅС‚РµРіСЂР°С†РёСЏ СѓРґР°Р»РµРЅР°.")
        return redirect("admin_integrations")
    return render(request, "bookings/admin_integration_confirm_delete.html", {"integration": integration})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_integration_test(request, pk):
    integration = get_object_or_404(ExternalIntegration, pk=pk)
    success, note = check_external_integration(integration)
    if success:
        messages.success(request, f"РџСЂРѕРІРµСЂРєР° РїСЂРѕС€Р»Р° СѓСЃРїРµС€РЅРѕ: {note}")
    else:
        messages.error(request, f"РџСЂРѕРІРµСЂРєР° Р·Р°РІРµСЂС€РёР»Р°СЃСЊ РѕС€РёР±РєРѕР№: {note}")
    return redirect("admin_integrations")


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_security(request):
    settings_obj = get_security_settings()
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "unlock":
            attempt = get_object_or_404(LoginAttempt, pk=request.POST.get("attempt_id"))
            unlock_login_attempt(attempt)
            messages.success(request, f"Р›РѕРіРёРЅ {attempt.username} СЂР°Р·Р±Р»РѕРєРёСЂРѕРІР°РЅ.")
            return redirect("admin_security")
        settings_obj.session_timeout_minutes = int(request.POST.get("session_timeout_minutes") or settings_obj.session_timeout_minutes)
        settings_obj.max_failed_login_attempts = int(request.POST.get("max_failed_login_attempts") or settings_obj.max_failed_login_attempts)
        settings_obj.login_lockout_minutes = int(request.POST.get("login_lockout_minutes") or settings_obj.login_lockout_minutes)
        settings_obj.lockout_enabled = request.POST.get("lockout_enabled") == "on"
        settings_obj.force_password_change_after_admin_reset = request.POST.get("force_password_change_after_admin_reset") == "on"
        settings_obj.save()
        messages.success(request, "Настройки безопасности обновлены.")
        return redirect("admin_security")

    attempts = LoginAttempt.objects.filter(failed_attempts__gt=0).order_by("-last_failed_at", "username")
    return render(request, "bookings/admin_security.html", {"security_settings": settings_obj, "login_attempts": attempts})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_backups(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_backup":
            archive = create_backup_archive(user=request.user)
            messages.success(request, f"Р РµР·РµСЂРІРЅР°СЏ РєРѕРїРёСЏ {archive.original_name} СЃРѕР·РґР°РЅР°.")
            return redirect("admin_backups")
        if action == "upload_backup" and request.FILES.get("backup_file"):
            uploaded = request.FILES["backup_file"]
            archive = BackupArchive.objects.create(
                file=uploaded,
                original_name=uploaded.name,
                created_by=request.user,
            )
            messages.success(request, f"РђСЂС…РёРІ {archive.original_name} Р·Р°РіСЂСѓР¶РµРЅ.")
            return redirect("admin_backups")
    return render(request, "bookings/admin_backups.html", {"archives": BackupArchive.objects.select_related("created_by", "restored_by").order_by("-created_at")})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_backup_download(request, pk):
    archive = get_object_or_404(BackupArchive, pk=pk)
    return FileResponse(archive.file.open("rb"), as_attachment=True, filename=archive.original_name or "backup.json.gz")


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_backup_restore(request, pk):
    archive = get_object_or_404(BackupArchive, pk=pk)
    token_key = f"restore_backup_{pk}"
    if request.method == "POST":
        token = request.POST.get("confirm_token")
        if token != request.session.get(token_key):
            messages.error(request, "Подтверждение восстановления истекло. Откройте страницу ещё раз.")
            return redirect("admin_backups")
        if request.POST.get("confirm_username", "").strip() != request.user.username:
            messages.error(request, "Для восстановления нужно подтвердить текущий логин администратора.")
            return redirect("admin_backup_restore", pk=pk)
        restore_backup_archive(archive=archive, user=request.user)
        request.session.pop(token_key, None)
        messages.success(request, f"Р’РѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёРµ РёР· Р°СЂС…РёРІР° {archive.original_name} РІС‹РїРѕР»РЅРµРЅРѕ.")
        return redirect("admin_backups")
    token = f"{pk}-{timezone.now().timestamp():.0f}"
    request.session[token_key] = token
    return render(request, "bookings/admin_backup_restore_confirm.html", {"archive": archive, "confirm_token": token})


@login_required
@user_passes_test(is_admin_app, login_url="/")
def admin_reports(request):
    report_type = request.GET.get("report", "users")
    date_from, date_to = parse_report_period(request)
    headers, rows = admin_report_rows(report_type, date_from, date_to)
    if request.GET.get("export") == "csv":
        return csv_response(f"admin_{report_type}_{date_from}_{date_to}.csv", headers, rows)
    return render(
        request,
        "bookings/admin_reports.html",
        {
            "report_type": report_type,
            "report_headers": headers,
            "report_rows": rows,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@login_required
@user_passes_test(is_operator_app, login_url="/")
def operator_cabinet(request):
    context = _operator_dashboard_context()
    context.update(
        {
            "reservations": booking_detail_queryset().order_by("-start_time")[:20],
            "tables": Table.objects.all().order_by("table_number"),
            "dishes": Dish.objects.all().order_by("name"),
            "complaints_total": VenueComplaint.objects.count(),
            "recent_complaints": VenueComplaint.objects.select_related("user").order_by("-created_at")[:8],
            "recent_reviews": OrderItemReview.objects.select_related("order_item__order__user", "order_item__dish").order_by("-created_at")[:8],
        }
    )
    return render(request, "bookings/operator_cabinet.html", context)


@login_required
@user_passes_test(is_operator_app, login_url="/")
def operator_reservations(request):
    reservations = booking_detail_queryset().order_by("-start_time")
    if request.GET.get("table"):
        reservations = reservations.filter(table_id=request.GET["table"])
    if request.GET.get("client"):
        reservations = reservations.filter(user_id=request.GET["client"])
    if request.GET.get("date_from"):
        reservations = reservations.filter(start_time__date__gte=request.GET["date_from"])
    if request.GET.get("date_to"):
        reservations = reservations.filter(start_time__date__lte=request.GET["date_to"])
    paginator = Paginator(reservations, 10)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "bookings/operator_reservations.html",
        {
            "reservations": page_obj,
            "tables": Table.objects.all().order_by("table_number"),
            "clients": User.objects.filter(profile__role=UserProfile.ROLE_CLIENT).order_by("first_name", "last_name", "username"),
            "page_obj": page_obj,
        },
    )


@login_required
@user_passes_test(is_operator_app, login_url="/")
def operator_reservation_detail(request, pk):
    reservation = booking_detail_queryset().filter(public_id=pk).first()
    if reservation is None:
        reservation = get_object_or_404(booking_detail_queryset(), pk=pk)
    return render(request, "bookings/operator_reservation_detail.html", {"reservation": reservation, "dishes": reservation.dishes})


@login_required
@user_passes_test(is_operator_app, login_url="/")
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
@user_passes_test(is_operator_app, login_url="/")
def operator_service_slots(request):
    slot_settings = get_slot_settings()
    ServiceWeekdayWindow.ensure_defaults()
    ServiceDurationOption.ensure_defaults()
    if request.method == "POST":
        slot_settings.booking_lead_time_minutes = int(request.POST.get("booking_lead_time_minutes") or slot_settings.booking_lead_time_minutes)
        slot_settings.max_working_days_ahead = int(request.POST.get("max_working_days_ahead") or slot_settings.max_working_days_ahead)
        slot_settings.slot_step_minutes = int(request.POST.get("slot_step_minutes") or slot_settings.slot_step_minutes)
        slot_settings.save()
        for window in ServiceWeekdayWindow.objects.order_by("weekday"):
            window.is_active = request.POST.get(f"window_active_{window.weekday}") == "on"
            window.open_time = request.POST.get(f"window_open_{window.weekday}") or window.open_time
            window.close_time = request.POST.get(f"window_close_{window.weekday}") or window.close_time
            window.full_clean()
            window.save()

        duration_values_raw = request.POST.get("duration_values", "")
        parsed_values = []
        for chunk in duration_values_raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                value = int(chunk)
            except ValueError:
                continue
            if value > 0 and value not in parsed_values:
                parsed_values.append(value)
        if not parsed_values:
            messages.error(request, "Укажите хотя бы одну активную длительность бронирования.")
            return redirect("operator_service_slots")
        existing_options = {item.duration_minutes: item for item in ServiceDurationOption.objects.all()}
        for duration in parsed_values:
            option = existing_options.get(duration)
            if option is None:
                ServiceDurationOption.objects.create(duration_minutes=duration, is_active=True, sort_order=duration)
            else:
                option.is_active = True
                option.sort_order = duration
                option.save(update_fields=["is_active", "sort_order"])
        ServiceDurationOption.objects.exclude(duration_minutes__in=parsed_values).update(is_active=False)
        messages.success(request, "Настройки временных слотов обновлены.")
        return redirect("operator_service_slots")

    windows = ServiceWeekdayWindow.objects.order_by("weekday")
    duration_values = get_duration_values()
    return render(
        request,
        "bookings/operator_service_slots.html",
        {
            "slot_settings": slot_settings,
            "weekday_windows": windows,
            "duration_values": ", ".join(str(item) for item in duration_values),
        },
    )


@login_required
@user_passes_test(is_operator_app, login_url="/")
def operator_reports(request):
    report_type = request.GET.get("report", "bookings")
    date_from, date_to = parse_report_period(request)
    headers, rows = operator_report_rows(report_type, date_from, date_to)
    if request.GET.get("export") == "csv":
        return csv_response(f"operator_{report_type}_{date_from}_{date_to}.csv", headers, rows)
    return render(
        request,
        "bookings/operator_reports.html",
        {
            "report_type": report_type,
            "report_headers": headers,
            "report_rows": rows,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


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
    return render(request, "bookings/client_order_detail.html", {"reservation": order, "line_info": line_info, "completed": completed})


@login_required
@user_passes_test(is_client, login_url="/")
def client_dish_review_list(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    reviews = OrderItemReview.objects.filter(order_item__dish=dish).select_related("order_item__order__user").order_by("-created_at")
    review_stats = reviews.aggregate(avg_rating=Avg("rating"), reviews_count=Count("id"))
    eligible_reviews = []
    candidate_items = (
        order_detail_queryset().filter(user=request.user, items__dish=dish).prefetch_related("items__dish", "items__review").distinct()
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

