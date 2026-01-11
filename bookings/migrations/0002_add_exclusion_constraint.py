# Migration to add exclusion constraint for preventing overlapping reservations
# This requires the btree_gist extension in PostgreSQL
# Works only with PostgreSQL, skipped for SQLite

from django.db import migrations, connection


def add_exclusion_constraint(apps, schema_editor):
    """Добавляет exclusion constraint только для PostgreSQL"""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")
            cursor.execute("""
                ALTER TABLE bookings_reservation
                ADD CONSTRAINT reservation_no_overlap
                EXCLUDE USING gist (
                    table_id WITH =,
                    tstzrange(start_time, end_time) WITH &&
                );
            """)


def remove_exclusion_constraint(apps, schema_editor):
    """Удаляет exclusion constraint"""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE bookings_reservation
                DROP CONSTRAINT IF EXISTS reservation_no_overlap;
            """)


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_exclusion_constraint, remove_exclusion_constraint),
    ]

