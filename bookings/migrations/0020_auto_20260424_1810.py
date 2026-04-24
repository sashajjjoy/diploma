from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0019_orderappliedpromotion_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderappliedpromotion",
            name="original_amount_snapshot",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=10,
                validators=[MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="orderappliedpromotion",
            name="quantity_applied",
            field=models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)]),
        ),
        migrations.AlterField(
            model_name="venuecomplaint",
            name="status",
            field=models.CharField(
                choices=[("new", "Новая"), ("seen", "Просмотрена"), ("closed", "Закрыта")],
                db_index=True,
                default="new",
                max_length=20,
            ),
        ),
    ]
