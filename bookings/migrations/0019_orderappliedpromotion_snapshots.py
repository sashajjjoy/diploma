from decimal import Decimal

from django.db import migrations, models


def backfill_promotion_snapshots(apps, schema_editor):
    OrderAppliedPromotion = apps.get_model("bookings", "OrderAppliedPromotion")
    Promotion = apps.get_model("bookings", "Promotion")

    for row in OrderAppliedPromotion.objects.select_related("promotion").all().iterator():
        promotion = row.promotion
        original = Decimal("0.00")
        if promotion.kind == "single_dish" and promotion.target_dish_id:
            original = Decimal(promotion.target_dish.price)
        elif promotion.kind == "combo":
            for item in promotion.combo_items.all():
                original += Decimal(item.dish.price) * item.min_quantity
        row.quantity_applied = 1
        row.original_amount_snapshot = original.quantize(Decimal("0.01"))
        row.save(update_fields=["quantity_applied", "original_amount_snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0018_orderitem_order_item_unique_dish_per_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderappliedpromotion",
            name="original_amount_snapshot",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=10),
        ),
        migrations.AddField(
            model_name="orderappliedpromotion",
            name="quantity_applied",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(backfill_promotion_snapshots, migrations.RunPython.noop),
    ]
