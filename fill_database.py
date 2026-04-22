"""
Заполнение базы демо-данными (очистка всего кроме Table и Dish + сид).

Запуск (рекомендуется):
  python3 manage.py reseed_demo
  python3 manage.py reseed_demo --no-input

Через shell:
  python3 manage.py shell < fill_database.py

Логика: bookings/seed_data.py, константа MIN_ROWS — минимум записей в основных таблицах.
"""

from django.core.management import call_command

call_command("reseed_demo", no_input=True)
