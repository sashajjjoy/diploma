from datetime import datetime, timedelta

import pytz
from django.db.models import Q
from django.utils import timezone

from bookings.models import Booking, Table

MOSCOW_TZ = pytz.timezone("Europe/Moscow")


def build_time_slots():
    slots = []
    for hour in range(12, 22):
        for minute in (0, 30):
            slots.append(f"{hour:02d}:{minute:02d}")
    return slots


def day_range_for_date(target_date):
    start_of_day = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time()))
    end_of_day = start_of_day + timedelta(days=1)
    return start_of_day, end_of_day


def resolve_relative_booking_date(date_str):
    now = timezone.localtime(timezone.now())
    if date_str == "today":
        return now.date()
    if date_str == "tomorrow":
        return (now + timedelta(days=1)).date()
    if date_str == "day_after_tomorrow":
        target_date = (now + timedelta(days=2)).date()
        while target_date.weekday() >= 5:
            target_date = target_date + timedelta(days=1)
        return target_date
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
    return start_local >= now_local + timedelta(minutes=30)


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


def available_slots_for_date(target_date, guests_count, durations=(25, 55)):
    suitable_tables = Table.objects.filter(seats__gte=guests_count).order_by("seats")
    time_slots = build_time_slots()
    available_slots = {}

    for duration in durations:
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
