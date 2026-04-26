from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0021_auto_20260426_1806"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "Запланировано"),
                    ("completed", "Завершено"),
                    ("cancelled", "Отменено"),
                ],
                db_index=True,
                default="scheduled",
                max_length=20,
            ),
        ),
    ]
