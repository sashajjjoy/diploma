"""
Скрипт для создания тестовых пользователей с ролями
Запуск: python3 manage.py shell < create_test_users.py
или: python3 manage.py shell, затем скопировать код
"""

from django.contrib.auth.models import User
from bookings.models import UserProfile, Client

# Создание клиента
client_user, created = User.objects.get_or_create(
    username='client1',
    defaults={
        'email': 'client1@example.com',
        'first_name': 'Иван',
        'last_name': 'Иванов'
    }
)
if created:
    client_user.set_password('client123')
    client_user.save()

# Создание профиля клиента
client_profile, created = UserProfile.objects.get_or_create(
    user=client_user,
    defaults={'role': 'client'}
)

# Создание записи Client
client, created = Client.objects.get_or_create(
    email=client_user.email,
    defaults={
        'full_name': f'{client_user.first_name} {client_user.last_name}',
        'user': client_user
    }
)
if not created and not client.user:
    client.user = client_user
    client.save()

print(f'✓ Клиент создан: {client_user.username} / пароль: client123')

# Создание оператора
operator_user, created = User.objects.get_or_create(
    username='operator1',
    defaults={
        'email': 'operator1@example.com',
        'first_name': 'Петр',
        'last_name': 'Петров'
    }
)
if created:
    operator_user.set_password('operator123')
    operator_user.save()

# Создание профиля оператора
operator_profile, created = UserProfile.objects.get_or_create(
    user=operator_user,
    defaults={'role': 'operator'}
)

print(f'✓ Оператор создан: {operator_user.username} / пароль: operator123')
print('\nДля входа используйте:')
print('  Клиент: client1 / client123')
print('  Оператор: operator1 / operator123')
print('  Админ: admin / admin123')






