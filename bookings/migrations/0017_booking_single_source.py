from django.db import migrations, models
import django.db.models.deletion


def backfill_public_ids_and_reviews(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")
    CustomerOrder = apps.get_model("bookings", "CustomerOrder")
    OrderAppliedPromotion = apps.get_model("bookings", "OrderAppliedPromotion")
    OrderItem = apps.get_model("bookings", "OrderItem")
    OrderItemReview = apps.get_model("bookings", "OrderItemReview")
    Reservation = apps.get_model("bookings", "Reservation")
    ReservationAppliedPromotion = apps.get_model("bookings", "ReservationAppliedPromotion")
    ReservationDish = apps.get_model("bookings", "ReservationDish")
    DishReview = apps.get_model("bookings", "DishReview")

    for booking in Booking.objects.all().iterator():
        if booking.public_id is None:
            booking.public_id = booking.legacy_reservation_id or booking.pk
            booking.save(update_fields=["public_id"])

    for order in CustomerOrder.objects.all().iterator():
        if order.public_id is None:
            if order.booking_id:
                booking = Booking.objects.filter(pk=order.booking_id).first()
                order.public_id = booking.public_id if booking else (order.legacy_reservation_id or order.pk)
            else:
                order.public_id = order.legacy_reservation_id or order.pk
            order.save(update_fields=["public_id"])

    for item in OrderItem.objects.all().iterator():
        if item.public_id is None:
            item.public_id = item.legacy_reservation_dish_id or item.pk
            item.save(update_fields=["public_id"])

    for review in DishReview.objects.all().iterator():
        try:
            order_item = OrderItem.objects.get(legacy_reservation_dish_id=review.reservation_dish_id)
        except OrderItem.DoesNotExist:
            continue
        OrderItemReview.objects.update_or_create(
            order_item_id=order_item.pk,
            defaults={
                "rating": review.rating,
                "comment": review.comment,
                "created_at": review.created_at,
            },
        )

    for reservation in Reservation.objects.all().iterator():
        if reservation.table_id is None:
            continue
        booking = Booking.objects.filter(legacy_reservation_id=reservation.pk).first()
        order = CustomerOrder.objects.filter(legacy_reservation_id=reservation.pk).first()
        if booking and order and order.booking_id != booking.pk:
            order.booking_id = booking.pk
            order.save(update_fields=["booking"])

    for order in CustomerOrder.objects.filter(booking__isnull=False).iterator():
        if order.public_id is None:
            order.public_id = order.booking.public_id or order.booking_id
            order.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0016_backfill_new_domain"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="public_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="customerorder",
            name="public_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="public_id",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.RunPython(backfill_public_ids_and_reviews, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="customerorder",
            name="booking",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="order", to="bookings.booking"),
        ),
        migrations.RemoveField(
            model_name="orderitemreview",
            name="legacy_dish_review",
        ),
        migrations.RemoveField(
            model_name="orderitem",
            name="legacy_reservation_dish",
        ),
        migrations.RemoveField(
            model_name="orderappliedpromotion",
            name="legacy_applied_promotion",
        ),
        migrations.RemoveField(
            model_name="customerorder",
            name="legacy_reservation",
        ),
        migrations.RemoveField(
            model_name="booking",
            name="legacy_reservation",
        ),
        migrations.DeleteModel(
            name="DishReview",
        ),
        migrations.DeleteModel(
            name="ReservationAppliedPromotion",
        ),
        migrations.DeleteModel(
            name="ReservationDish",
        ),
        migrations.DeleteModel(
            name="Reservation",
        ),
    ]
