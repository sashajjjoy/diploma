"""
Скрипт для заполнения базы данных тестовыми данными
Запуск: python3 manage.py shell < fill_database.py
или: python3 manage.py shell, затем выполнить код
"""

from django.utils import timezone
from datetime import timedelta
from bookings.models import Table, Dish, Client, Reservation, ReservationDish, UserProfile, User

print("Начинаем заполнение базы данных...")

# Очистка существующих данных (опционально)
# ReservationDish.objects.all().delete()
# Reservation.objects.all().delete()
# Dish.objects.all().delete()
# Table.objects.all().delete()

# 1. Создание столиков (максимум 4 места)
print("\n1. Создание столиков...")
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

tables = []
for table_data in tables_data:
    table, created = Table.objects.get_or_create(
        table_number=table_data['table_number'],
        defaults={'seats': table_data['seats']}
    )
    if created:
        print(f"  ✓ Создан столик №{table.table_number} ({table.seats} мест)")
    tables.append(table)

# 2. Создание блюд
print("\n2. Создание блюд...")
dishes_data = [
    {
        'name': 'Борщ украинский',
        'description': 'Традиционный борщ с мясом, сметаной и зеленью',
        'price': 350.00,
        'available_quantity': 20
    },
    {
        'name': 'Солянка мясная',
        'description': 'Густой суп с мясом, колбасой, маслинами и лимоном',
        'price': 380.00,
        'available_quantity': 15
    },
    {
        'name': 'Стейк из говядины',
        'description': 'Говяжий стейк средней прожарки с овощами гриль',
        'price': 850.00,
        'available_quantity': 10
    },
    {
        'name': 'Лосось в сливочном соусе',
        'description': 'Филе лосося с соусом на основе сливок и белого вина',
        'price': 920.00,
        'available_quantity': 12
    },
    {
        'name': 'Паста карбонара',
        'description': 'Паста с беконом, яйцом и пармезаном',
        'price': 420.00,
        'available_quantity': 25
    },
    {
        'name': 'Салат Цезарь',
        'description': 'Классический салат с курицей, сухариками и соусом',
        'price': 380.00,
        'available_quantity': 30
    },
    {
        'name': 'Греческий салат',
        'description': 'Свежие овощи с оливками, сыром фета и оливковым маслом',
        'price': 350.00,
        'available_quantity': 25
    },
    {
        'name': 'Пицца Маргарита',
        'description': 'Классическая пицца с томатами, моцареллой и базиликом',
        'price': 450.00,
        'available_quantity': 15
    },
    {
        'name': 'Пицца Пепперони',
        'description': 'Пицца с острой колбасой пепперони и сыром',
        'price': 520.00,
        'available_quantity': 12
    },
    {
        'name': 'Шашлык из свинины',
        'description': 'Маринованная свинина на мангале с соусом',
        'price': 680.00,
        'available_quantity': 18
    },
    {
        'name': 'Котлеты по-киевски',
        'description': 'Куриные котлеты с маслом и зеленью',
        'price': 480.00,
        'available_quantity': 20
    },
    {
        'name': 'Оливье',
        'description': 'Классический салат оливье',
        'price': 320.00,
        'available_quantity': 35
    },
    {
        'name': 'Компот из сухофруктов',
        'description': 'Натуральный компот из сухофруктов',
        'price': 80.00,
        'available_quantity': 50
    },
    {
        'name': 'Морс клюквенный',
        'description': 'Освежающий морс из свежей клюквы',
        'price': 90.00,
        'available_quantity': 40
    },
    {
        'name': 'Кофе американо',
        'description': 'Эспрессо с добавлением горячей воды',
        'price': 120.00,
        'available_quantity': 100
    },
]

dishes = []
for dish_data in dishes_data:
    dish, created = Dish.objects.get_or_create(
        name=dish_data['name'],
        defaults={
            'description': dish_data['description'],
            'price': dish_data['price'],
            'available_quantity': dish_data['available_quantity']
        }
    )
    if created:
        print(f"  ✓ Создано блюдо: {dish.name} ({dish.price}₽, {dish.available_quantity} шт.)")
    dishes.append(dish)

# 3. Получение или создание клиентов
print("\n3. Работа с клиентами...")
client_user, _ = User.objects.get_or_create(
    username='client1',
    defaults={'email': 'client1@example.com', 'first_name': 'Иван', 'last_name': 'Иванов'}
)

# Создание профиля, если его нет
UserProfile.objects.get_or_create(
    user=client_user,
    defaults={'role': 'client'}
)

client, created = Client.objects.get_or_create(
    user=client_user,
    defaults={
        'full_name': f'{client_user.first_name} {client_user.last_name}',
        'email': client_user.email
    }
)
if created:
    print(f"  ✓ Клиент: {client.full_name}")

# Создаем еще несколько тестовых клиентов
test_clients_data = [
    {'full_name': 'Петр Петров', 'email': 'petrov@example.com'},
    {'full_name': 'Мария Сидорова', 'email': 'sidorova@example.com'},
    {'full_name': 'Алексей Козлов', 'email': 'kozlov@example.com'},
]

for client_data in test_clients_data:
    test_client, created = Client.objects.get_or_create(
        email=client_data['email'],
        defaults={'full_name': client_data['full_name']}
    )
    if created:
        print(f"  ✓ Клиент: {test_client.full_name}")

all_clients = Client.objects.all()

# 4. Создание бронирований
print("\n4. Создание бронирований...")

def get_working_days_until(target_date):
    """Подсчитывает количество рабочих дней до указанной даты"""
    now = timezone.now()
    now_date = now.date()
    target_date_only = target_date.date() if hasattr(target_date, 'date') else target_date
    
    if now_date >= target_date_only:
        return 0
    
    working_days = 0
    current = now_date
    target = target_date_only
    
    while current < target:
        if current.weekday() < 5:  # Понедельник-пятница
            working_days += 1
        current += timedelta(days=1)
    
    return working_days

# Бронирования в прошлом (завершенные) - используем прямой SQL для обхода валидации
from django.db import connection
past_date = timezone.now() - timedelta(days=10)
try:
    with connection.cursor() as cursor:
        cursor.execute("""
            INSERT INTO bookings_reservation (client_id, table_id, guests_count, start_time, end_time, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, [
            client.id, tables[2].id, 3,
            past_date.replace(hour=12, minute=0),
            past_date.replace(hour=13, minute=30),
            timezone.now()
        ])
    past_reservation = Reservation.objects.filter(client=client, table=tables[2], start_time__lt=timezone.now()).order_by('-start_time').first()
    if past_reservation:
        print(f"  ✓ Создано прошедшее бронирование: Столик {past_reservation.table.table_number}")
except Exception as e:
    print(f"  ⚠ Ошибка при создании прошедшего бронирования: {e}")
    past_reservation = None

# Будущие бронирования - минимум через 2 рабочих дня
now = timezone.now()
base_date = now
working_days_count = 0
days_to_add = 0

# Ищем дату через минимум 2 рабочих дня
while working_days_count < 2:
    days_to_add += 1
    check_date = base_date + timedelta(days=days_to_add)
    working_days_count = get_working_days_until(check_date)

# Создаем несколько будущих бронирований
future_dates = []
for i in range(3):
    future_date = base_date + timedelta(days=days_to_add + i*2)
    # Убеждаемся, что это рабочий день
    while future_date.weekday() >= 5:
        future_date += timedelta(days=1)
    future_dates.append(future_date)

reservations = []
for i, future_date in enumerate(future_dates):
    try:
        reservation = Reservation.objects.create(
            client=client if i == 0 else all_clients[i % len(all_clients)],
            table=tables[i],
            guests_count=2 + i,
            start_time=future_date.replace(hour=13, minute=0),
            end_time=future_date.replace(hour=14, minute=30),
        )
        reservations.append(reservation)
        print(f"  ✓ Создано будущее бронирование: Столик {reservation.table.table_number} на {future_date.strftime('%d.%m.%Y %H:%M')}")
    except Exception as e:
        print(f"  ⚠ Ошибка при создании бронирования: {e}")

# Для активного бронирования тоже нужно соблюсти правила, но мы создадим его вручную
# если хотим протестировать активное бронирование, можно создать его через админку

# 5. Создание предзаказов блюд
print("\n5. Создание предзаказов блюд...")

# Предзаказ для будущих бронирований
future_reservations = Reservation.objects.filter(start_time__gt=timezone.now())
if future_reservations.exists():
    for i, future_res in enumerate(future_reservations[:3]):
        # Добавляем разные блюда в каждое бронирование
        if i == 0:
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[0],  # Борщ
                defaults={'quantity': 2}
            )
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[5],  # Салат Цезарь
                defaults={'quantity': 2}
            )
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[14],  # Кофе
                defaults={'quantity': 2}
            )
        elif i == 1:
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[2],  # Стейк
                defaults={'quantity': 2}
            )
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[5],  # Салат Цезарь
                defaults={'quantity': 2}
            )
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[7],  # Пицца Маргарита
                defaults={'quantity': 1}
            )
        else:
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[3],  # Лосось
                defaults={'quantity': 2}
            )
            ReservationDish.objects.get_or_create(
                reservation=future_res,
                dish=dishes[6],  # Греческий салат
                defaults={'quantity': 2}
            )
    print(f"  ✓ Добавлены блюда в {future_reservations.count()} будущих бронирований")

# Предзаказ для прошедшего бронирования
if past_reservation:
    ReservationDish.objects.get_or_create(
        reservation=past_reservation,
        dish=dishes[1],  # Солянка
        defaults={'quantity': 3}
    )
    ReservationDish.objects.get_or_create(
        reservation=past_reservation,
        dish=dishes[11],  # Оливье
        defaults={'quantity': 1}
    )
    print(f"  ✓ Добавлены блюда в прошедшее бронирование")

print("\n" + "="*50)
print("База данных успешно заполнена!")
print("="*50)
print(f"\nСтатистика:")
print(f"  Столиков: {Table.objects.count()}")
print(f"  Блюд: {Dish.objects.count()}")
print(f"  Клиентов: {Client.objects.count()}")
print(f"  Бронирований: {Reservation.objects.count()}")
print(f"  Предзаказов: {ReservationDish.objects.count()}")

