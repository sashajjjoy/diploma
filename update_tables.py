"""
Ограничение числа мест у столиков до 4 (правило модели Table).
Не удаляет столики и не пересоздаёт их.

Запуск: python3 manage.py shell < update_tables.py
"""

from django.utils import timezone

from bookings.models import Table, Reservation

print("Проверка столиков с числом мест > 4...")

for table in Table.objects.filter(seats__gt=4):
    active = table.reservations.filter(
        end_time__gte=timezone.now(),
        guests_count__gt=4,
    )
    if active.exists():
        print(f"  ⚠ Столик №{table.table_number}: есть активные брони с >4 гостями, пропуск.")
        continue
    old = table.seats
    table.seats = 4
    table.save()
    print(f"  ✓ Столик №{table.table_number}: {old} → 4 мест")

print(f"Всего столиков: {Table.objects.count()}")
