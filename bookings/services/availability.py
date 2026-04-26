from datetime import datetime, timedelta

import pytz
from django.db.models import Q
from django.utils import timezone

from bookings.models import (
    Booking,
    ServiceDurationOption,
    ServiceSlotSettings,
    ServiceWeekdayWindow,
    Table,
)

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def get_slot_settings():
    return ServiceSlotSettings.get_solo()


def get_duration_options():
    ServiceDurationOption.ensure_defaults()
    return list(ServiceDurationOption.objects.filter(is_active=True).order_by("sort_order", "duration_minutes"))


def get_duration_values():
    values = [option.duration_minutes for option in get_duration_options()]
    return values or [25, 55]


def get_weekday_window(target_date):
    ServiceWeekdayWindow.ensure_defaults()
    return ServiceWeekdayWindow.objects.filter(weekday=target_date.weekday(), is_active=True).first()


def build_time_slots(target_date=None, duration_minutes=None):
    settings = get_slot_settings()
    reference_date = target_date or timezone.localdate()
    window = get_weekday_window(reference_date)
    if window is None:
        return []

    open_dt = datetime.combine(reference_date, window.open_time)
    close_dt = datetime.combine(reference_date, window.close_time)
    slot_step = timedelta(minutes=settings.slot_step_minutes)
    duration_delta = timedelta(minutes=int(duration_minutes or 0))
    current = open_dt
    slots = []
    while current <= close_dt:
        if duration_minutes is None or current + duration_delta <= close_dt:
            slots.append(current.strftime("%H:%M"))
        current += slot_step
    return slots


def day_range_for_date(target_date):
    start_of_day = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time()))
    end_of_day = start_of_day + timedelta(days=1)
    return start_of_day, end_of_day


def resolve_relative_booking_date(date_str):
    now = timezone.localtime(timezone.now())
    available_dates = get_bookable_dates(now=now)
    if date_str == "today" and len(available_dates) >= 1:
        return available_dates[0]
    if date_str == "tomorrow" and len(available_dates) >= 2:
        return available_dates[1]
    if date_str == "day_after_tomorrow" and len(available_dates) >= 3:
        return available_dates[2]
    raise ValueError("Invalid relative booking date.")


def parse_booking_date(date_raw):
    try:
        return datetime.fromisoformat(date_raw).date()
    except ValueError:
        return resolve_relative_booking_date(date_raw)


def build_reservation_datetimes(target_date, time_str=None, duration_minutes=None, takeout=False):
    if takeout:
        start_datetime = MOSCOW_TZ.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=0, minute=0))
        )
        end_datetime = MOSCOW_TZ.localize(
            datetime.combine(target_date, datetime.min.time().replace(hour=23, minute=59))
        )
        return start_datetime, end_datetime

    hour, minute = map(int, time_str.split(":"))
    start_datetime = MOSCOW_TZ.localize(
        datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
    )
    end_datetime = start_datetime + timedelta(minutes=int(duration_minutes))
    return start_datetime, end_datetime


def is_booking_time_allowed(start_datetime):
    start_local = timezone.localtime(start_datetime, MOSCOW_TZ)
    now_local = timezone.localtime(timezone.now(), MOSCOW_TZ)
    settings = get_slot_settings()
    return start_local >= now_local + timedelta(minutes=settings.booking_lead_time_minutes)


def get_bookable_dates(now=None):
    now = now or timezone.localtime(timezone.now())
    settings = get_slot_settings()
    ServiceWeekdayWindow.ensure_defaults()
    dates = []
    current = now.date()
    working_days = 0
    while working_days <= settings.max_working_days_ahead and len(dates) < max(settings.max_working_days_ahead + 1, 3):
        if ServiceWeekdayWindow.is_service_day(current):
            dates.append(current)
            working_days += 1
        current += timedelta(days=1)
    return dates


def get_date_label(target_date, now_date=None):
    now_date = now_date or timezone.localdate()
    if target_date == now_date:
        return "Сегодня"
    if target_date == now_date + timedelta(days=1):
        return "Завтра"
    return target_date.strftime("%d.%m.%Y")


def find_available_table(guests_count, start_datetime, end_datetime, exclude_booking_id=None):
    if not is_booking_time_allowed(start_datetime):
        return None
    suitable_tables = Table.objects.filter(seats__gte=guests_count).order_by("seats")
    for table in suitable_tables:
        overlapping_query = Booking.objects.filter(table=table).exclude(status=Booking.STATUS_CANCELLED).filter(
            start_time__lt=end_datetime,
            end_time__gt=start_datetime,
        )
        if exclude_booking_id:
            overlapping_query = overlapping_query.exclude(pk=exclude_booking_id)
        if not overlapping_query.exists():
            return table
    return None


def occupied_slots_for_table_date(table, target_date, booking_id=None):
    start_of_day, end_of_day = day_range_for_date(target_date)
    reservations = Booking.objects.filter(
        table=table,
        start_time__gte=start_of_day,
        start_time__lt=end_of_day,
    ).exclude(status=Booking.STATUS_CANCELLED)
    if booking_id:
        reservations = reservations.exclude(Q(public_id=booking_id) | Q(pk=booking_id))

    occupied_slots = []
    for reservation in reservations:
        start_moscow = timezone.localtime(reservation.start_time)
        end_moscow = timezone.localtime(reservation.end_time)
        occupied_slots.append(
            {
                "start": start_moscow.strftime("%H:%M"),
                "end": end_moscow.strftime("%H:%M"),
                "start_datetime": start_moscow,
                "end_datetime": end_moscow,
            }
        )
    return occupied_slots


def available_slots_for_date(target_date, guests_count, durations=None):
    durations = tuple(durations or get_duration_values())
    if get_weekday_window(target_date) is None:
        return {duration: [] for duration in durations}
    suitable_tables = Table.objects.filter(seats__gte=guests_count).order_by("seats")
    available_slots = {}

    for duration in durations:
        time_slots = build_time_slots(target_date, duration_minutes=duration)
        available_slots[duration] = []
        for time_str in time_slots:
            start_datetime, end_datetime = build_reservation_datetimes(
                target_date, time_str=time_str, duration_minutes=duration
            )
            if not is_booking_time_allowed(start_datetime):
                continue
            is_available = False
            for table in suitable_tables:
                overlapping = Booking.objects.filter(
                    table=table,
                    start_time__lt=end_datetime,
                    end_time__gt=start_datetime,
                ).exclude(status=Booking.STATUS_CANCELLED).exists()
                if not overlapping:
                    is_available = True
                    break
            if is_available:
                available_slots[duration].append(time_str)
    return available_slots
