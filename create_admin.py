"""
Скрипт для создания админа
Запуск: python3 manage.py shell < create_admin.py
или: python3 manage.py shell, затем скопировать код
"""

from django.contrib.auth.models import User
from bookings.models import UserProfile

# Создание админа
admin_user, created = User.objects.get_or_create(
    username='admin',
    defaults={
        'email': 'admin@example.com',
        'is_superuser': True,
        'is_staff': True,
        'is_active': True,
        'first_name': 'Администратор',
        'last_name': ''
    }
)

if created:
    admin_user.set_password('admin123')
    admin_user.save()
    print(f'✓ Админ создан: {admin_user.username} / пароль: admin123')
else:
    # Если админ уже существует, сбросим пароль на admin123
    admin_user.set_password('admin123')
    admin_user.is_superuser = True
    admin_user.is_staff = True
    admin_user.is_active = True
    admin_user.save()
    print(f'✓ Пароль админа сброшен: {admin_user.username} / пароль: admin123')

# Создание профиля админа (если нужно)
admin_profile, created = UserProfile.objects.get_or_create(
    user=admin_user,
    defaults={'role': 'admin'}
)

print('\nУчетные данные для входа:')
print('=' * 40)
print('Админ:')
print('  Username: admin')
print('  Password: admin123')
print('  Email: admin@example.com')
print('=' * 40)




