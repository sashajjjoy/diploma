import csv
from datetime import datetime, timedelta

from django.db.models import Avg, Count, Sum
from django.http import HttpResponse
from django.utils import timezone

from bookings.models import (
    BackupArchive,
    Booking,
    CustomerOrder,
    ExternalIntegration,
    OrderItem,
    OrderItemReview,
    UserProfile,
    VenueComplaint,
)


def parse_report_period(request, default_days=30):
    date_from_raw = request.GET.get("date_from")
    date_to_raw = request.GET.get("date_to")
    today = timezone.localdate()
    try:
        date_from = datetime.fromisoformat(date_from_raw).date() if date_from_raw else today - timedelta(days=default_days)
    except ValueError:
        date_from = today - timedelta(days=default_days)
    try:
        date_to = datetime.fromisoformat(date_to_raw).date() if date_to_raw else today
    except ValueError:
        date_to = today
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def csv_response(filename, headers, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return response


def operator_report_rows(report_type, date_from, date_to):
    if report_type == "bookings":
        queryset = Booking.objects.select_related("user", "table").filter(start_time__date__gte=date_from, start_time__date__lte=date_to).order_by("-start_time")
        rows = [
            [
                item.public_id or item.pk,
                item.start_time.strftime("%d.%m.%Y %H:%M"),
                item.end_time.strftime("%d.%m.%Y %H:%M"),
                item.user.username,
                item.table.table_number,
                item.guests_count,
                item.get_status_display(),
            ]
            for item in queryset
        ]
        return ["ID", "Начало", "Окончание", "Клиент", "Столик", "Гостей", "Статус"], rows
    if report_type == "sales":
        queryset = (
            OrderItem.objects.filter(order__scheduled_for__date__gte=date_from, order__scheduled_for__date__lte=date_to)
            .values("dish_name_snapshot")
            .annotate(total_quantity=Sum("quantity"), total_revenue=Sum("line_total_snapshot"), order_count=Count("order_id", distinct=True))
            .order_by("-total_quantity", "dish_name_snapshot")
        )
        rows = [[item["dish_name_snapshot"], item["total_quantity"], item["order_count"], item["total_revenue"]] for item in queryset]
        return ["Блюдо", "Количество", "Заказов", "Выручка"], rows
    if report_type == "complaints":
        queryset = VenueComplaint.objects.select_related("user").filter(created_at__date__gte=date_from, created_at__date__lte=date_to).order_by("-created_at")
        rows = [[item.created_at.strftime("%d.%m.%Y %H:%M"), item.user.username, item.subject, item.get_status_display(), item.message] for item in queryset]
        return ["Дата", "Клиент", "Тема", "Статус", "Текст"], rows
    queryset = OrderItemReview.objects.select_related("order_item__dish", "order_item__order__user").filter(created_at__date__gte=date_from, created_at__date__lte=date_to).order_by("-created_at")
    rows = [[item.created_at.strftime("%d.%m.%Y %H:%M"), item.order_item.dish_name_snapshot, item.rating, item.order_item.order.user.username, item.comment] for item in queryset]
    return ["Дата", "Блюдо", "Оценка", "Клиент", "Комментарий"], rows


def admin_report_rows(report_type, date_from, date_to):
    if report_type == "users":
        queryset = UserProfile.objects.select_related("user").order_by("role", "user__username")
        rows = [[item.user.username, item.user.email, item.get_role_display(), "Да" if item.user.is_active else "Нет"] for item in queryset]
        return ["Логин", "Email", "Роль", "Активен"], rows
    if report_type == "integrations":
        queryset = ExternalIntegration.objects.order_by("name")
        rows = [[item.name, item.base_url, item.get_auth_type_display(), "Да" if item.is_active else "Нет", item.last_check_success, item.last_checked_at, item.last_check_note] for item in queryset]
        return ["Название", "URL", "Авторизация", "Активна", "Успех проверки", "Проверена", "Примечание"], rows
    if report_type == "backups":
        queryset = BackupArchive.objects.select_related("created_by", "restored_by").order_by("-created_at")
        rows = [[item.original_name, item.created_at.strftime("%d.%m.%Y %H:%M"), getattr(item.created_by, "username", ""), item.last_restored_at.strftime("%d.%m.%Y %H:%M") if item.last_restored_at else "", getattr(item.restored_by, "username", ""), item.restore_count] for item in queryset]
        return ["Архив", "Создан", "Создал", "Последнее восстановление", "Восстановил", "Кол-во восстановлений"], rows
    queryset = (
        CustomerOrder.objects.select_related("user", "booking__table")
        .filter(scheduled_for__date__gte=date_from, scheduled_for__date__lte=date_to)
        .order_by("-scheduled_for")
    )
    rows = [[item.public_id or item.pk, item.scheduled_for.strftime("%d.%m.%Y %H:%M"), item.user.username, item.order_type, item.status, item.total_amount] for item in queryset]
    return ["Заказ", "Дата", "Пользователь", "Тип", "Статус", "Сумма"], rows
