from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0017_booking_single_source"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.UniqueConstraint(
                fields=("order", "dish"),
                name="order_item_unique_dish_per_order",
            ),
        ),
    ]
