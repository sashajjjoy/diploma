"""
Скрипт для обновления столиков - максимум 4 места
Запуск: python3 manage.py shell < update_tables.py
"""

from bookings.models import Table, Reservation

print("Обновление столиков до максимум 4 мест...")

# Обновляем существующие столики с количеством мест > 4
tables_to_update = Table.objects.filter(seats__gt=4)
updated_count = 0

for table in tables_to_update:
    old_seats = table.seats
    # Проверяем, нет ли активных бронирований с количеством гостей > 4
    from django.utils import timezone
    active_reservations = table.reservations.filter(
        end_time__gte=timezone.now(),
        guests_count__gt=4
    )
    
    if active_reservations.exists():
        print(f"  ⚠ Столик №{table.table_number} имеет активные бронирования с {old_seats} мест. Пропускаем.")
        continue
    
    table.seats = 4
    table.save()
    print(f"  ✓ Обновлен столик №{table.table_number}: {old_seats} → 4 мест")
    updated_count += 1

print(f"\nОбновлено столиков: {updated_count}")

# Пересоздаем таблицу столиков (удаляем старые, создаем новые)
print("\nПересоздание столиков...")

# Удаляем столики без активных бронирований
from django.utils import timezone
for table in Table.objects.all():
    active_reservations = table.reservations.filter(end_time__gte=timezone.now())
    if not active_reservations.exists():
        table.delete()
        print(f"  ✓ Удален столик №{table.table_number}")

# Создаем новые столики
tables_data = [
    {'table_number': '1', 'seats': 2},
    {'table_number': '2', 'seats': 2},
    {'table_number': '3', 'seats': 4},
    {'table_number': '4', 'seats': 4},
    {'table_number': '5', 'seats': 2},
    {'table_number': '6', 'seats': 4},
    {'table_number': '7', 'seats': 4},
    {'table_number': '8', 'seats': 3},
    {'table_number': '9', 'seats': 2},
    {'table_number': '10', 'seats': 4},
    {'table_number': '11', 'seats': 2},
    {'table_number': '12', 'seats': 4},
]

for table_data in tables_data:
    table, created = Table.objects.get_or_create(
        table_number=table_data['table_number'],
        defaults={'seats': table_data['seats']}
    )
    if created:
        print(f"  ✓ Создан столик №{table.table_number} ({table.seats} мест)")
    else:
        if table.seats != table_data['seats']:
            table.seats = table_data['seats']
            table.save()
            print(f"  ✓ Обновлен столик №{table.table_number} ({table.seats} мест)")

print(f"\nВсего столиков: {Table.objects.count()}")
print("Готово!")





