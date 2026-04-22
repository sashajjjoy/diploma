"""
Скрипт для создания тестовых пользователей с ролями.
Запуск: python3 manage.py shell < create_test_users.py

Полная перезагрузка демо-данных (кроме столиков и блюд):
  python3 manage.py reseed_demo
"""

from django.contrib.auth.models import User
from bookings.models import UserProfile

client_user, created = User.objects.get_or_create(
    username="client1",
    defaults={
        "email": "client1@example.com",
        "first_name": "Иван",
        "last_name": "Иванов",
    },
)
if created:
    client_user.set_password("client123")
    client_user.save()

UserProfile.objects.get_or_create(
    user=client_user,
    defaults={"role": "client"},
)

operator_user, created = User.objects.get_or_create(
    username="operator1",
    defaults={
        "email": "operator1@example.com",
        "first_name": "Петр",
        "last_name": "Петров",
    },
)
if created:
    operator_user.set_password("operator123")
    operator_user.save()

UserProfile.objects.get_or_create(
    user=operator_user,
    defaults={"role": "operator"},
)

print(f"✓ Клиент: {client_user.username} / client123")
print(f"✓ Оператор: {operator_user.username} / operator123")
print("Полный набор демо-пользователей и данных: python3 manage.py reseed_demo")
