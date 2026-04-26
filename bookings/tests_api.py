from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytz
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from bookings.models import (
    Booking,
    CustomerOrder,
    Dish,
    LoginAttempt,
    OrderItem,
    OrderItemReview,
    Promotion,
    ServiceDurationOption,
    ServiceSlotSettings,
    ServiceWeekdayWindow,
    Table,
    UserProfile,
    WeeklyMenuDaySettings,
    WeeklyMenuItem,
)


MOSCOW_TZ = pytz.timezone("Europe/Moscow")


class ApiBaseTestCase(TestCase):
    def setUp(self):
        self.client_api = APIClient()
        self.client_user = User.objects.create_user("client", email="client@example.com", password="pass12345")
        self.other_user = User.objects.create_user("other", password="pass12345")
        self.operator_user = User.objects.create_user("operator", password="pass12345")
        UserProfile.objects.create(user=self.client_user, role="client")
        UserProfile.objects.create(user=self.other_user, role="client")
        UserProfile.objects.create(user=self.operator_user, role="operator")
        self.table2 = Table.objects.create(table_number="T2", seats=2)
        self.table4 = Table.objects.create(table_number="T4", seats=4)
        self.dish1 = Dish.objects.create(name="Soup", price=Decimal("120.00"), available_quantity=20)
        self.dish2 = Dish.objects.create(name="Cutlet", price=Decimal("250.00"), available_quantity=20)
        self.booking_date = self._next_weekday(timezone.localdate())
        day_settings = WeeklyMenuDaySettings.objects.create(day_of_week=self.booking_date.weekday(), is_active=True)
        WeeklyMenuItem.objects.create(day_settings=day_settings, dish=self.dish1, order=1)
        WeeklyMenuItem.objects.create(day_settings=day_settings, dish=self.dish2, order=2)

    def _next_weekday(self, start_date):
        candidate = start_date + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _previous_weekday(self, start_date):
        candidate = start_date - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    def _booking_datetimes(self, target_date=None, hour=12, minute=0, duration=55):
        target_date = target_date or self.booking_date
        start = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute)))
        end = start + timedelta(minutes=duration)
        return start, end

    def auth_as_client(self):
        self.client_api.force_authenticate(user=self.client_user)

    def create_booking(self, user=None, target_date=None, hour=12, minute=0, duration=55, table=None):
        user = user or self.client_user
        table = table or self.table2
        start, end = self._booking_datetimes(target_date, hour=hour, minute=minute, duration=duration)
        booking = Booking.objects.create(
            user=user,
            table=table,
            guests_count=min(table.seats, 2),
            start_time=start,
            end_time=end,
        )
        order = CustomerOrder.objects.create(
            public_id=booking.public_id,
            user=user,
            booking=booking,
            order_type=CustomerOrder.TYPE_DINE_IN,
            scheduled_for=start,
            subtotal_amount=Decimal("120.00"),
            total_amount=Decimal("120.00"),
        )
        line = OrderItem.objects.create(
            order=order,
            dish=self.dish1,
            dish_name_snapshot=self.dish1.name,
            unit_price_snapshot=self.dish1.price,
            quantity=1,
            line_total_snapshot=self.dish1.price,
        )
        return booking, order, line


class ReservationApiTests(ApiBaseTestCase):
    def test_client_can_create_reservation(self):
        self.auth_as_client()
        response = self.client_api.post(
            "/api/v1/reservations/",
            {
                "date": self.booking_date.isoformat(),
                "time": "12:00",
                "duration_minutes": 55,
                "guests_count": 2,
                "dishes": [{"dish": self.dish1.id, "quantity": 2}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["order_total"], "240.00")
        self.assertEqual(Booking.objects.filter(user=self.client_user).count(), 1)

    def test_client_can_open_own_reservation_and_order(self):
        booking, order, _ = self.create_booking()
        self.auth_as_client()
        reservation_response = self.client_api.get(f"/api/v1/reservations/{booking.public_id}/")
        order_response = self.client_api.get(f"/api/v1/orders/{order.public_id}/")
        self.assertEqual(reservation_response.status_code, 200)
        self.assertEqual(order_response.status_code, 200)

    def test_user_cannot_access_foreign_reservation(self):
        booking, _, _ = self.create_booking(user=self.other_user)
        self.auth_as_client()
        response = self.client_api.get(f"/api/v1/reservations/{booking.public_id}/")
        self.assertEqual(response.status_code, 404)

    def test_available_slots_accept_relative_date_key(self):
        self.auth_as_client()
        response = self.client_api.get("/api/v1/availability/available-slots/?date=tomorrow&guests_count=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("available_slots", response.data)

    def test_client_cannot_order_plain_dish_when_active_single_promo_exists(self):
        self.auth_as_client()
        Promotion.objects.create(
            name="Fish promo",
            description="Promo",
            kind=Promotion.KIND_SINGLE,
            discount_type=Promotion.DISCOUNT_PERCENT,
            discount_value=Decimal("10.00"),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
            target_dish=self.dish1,
        )
        response = self.client_api.post(
            "/api/v1/reservations/",
            {
                "date": self.booking_date.isoformat(),
                "time": "12:00",
                "duration_minutes": 55,
                "guests_count": 2,
                "dishes": [{"dish": self.dish1.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("dishes", response.data["errors"])

    def test_available_slots_hide_times_less_than_30_minutes_ahead(self):
        self.auth_as_client()
        target_date = self._next_weekday(timezone.localdate() - timedelta(days=1))
        fake_now = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time().replace(hour=14, minute=45)))
        with patch("django.utils.timezone.now", return_value=fake_now):
            response = self.client_api.get(f"/api/v1/availability/available-slots/?date={target_date.isoformat()}&guests_count=2")
        self.assertEqual(response.status_code, 200)
        slots_25 = response.data["available_slots"][25]
        self.assertNotIn("15:00", slots_25)
        self.assertIn("15:30", slots_25)

    def test_client_cannot_book_slot_less_than_30_minutes_ahead(self):
        self.auth_as_client()
        target_date = self._next_weekday(timezone.localdate() - timedelta(days=1))
        fake_now = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time().replace(hour=14, minute=45)))
        with patch("django.utils.timezone.now", return_value=fake_now):
            response = self.client_api.post(
                "/api/v1/reservations/",
                {
                    "date": target_date.isoformat(),
                    "time": "15:00",
                    "duration_minutes": 55,
                    "guests_count": 2,
                    "dishes": [{"dish": self.dish2.id, "quantity": 1}],
                },
                format="json",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("time", response.data["errors"])

    def test_available_slots_use_operator_duration_settings(self):
        self.auth_as_client()
        ServiceWeekdayWindow.ensure_defaults()
        ServiceDurationOption.objects.all().update(is_active=False)
        ServiceDurationOption.objects.create(duration_minutes=40, is_active=True, sort_order=10)
        ServiceDurationOption.objects.create(duration_minutes=70, is_active=True, sort_order=20)
        ServiceSlotSettings.get_solo().slot_step_minutes = 20
        ServiceSlotSettings.get_solo().save()
        response = self.client_api.get(f"/api/v1/availability/available-slots/?date={self.booking_date.isoformat()}&guests_count=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(40, response.data["available_slots"])
        self.assertIn(70, response.data["available_slots"])

    def test_login_attempts_lock_api_auth(self):
        for _ in range(5):
            response = self.client_api.post("/api/v1/auth/login/", {"username": "client", "password": "wrong"}, format="json")
            self.assertEqual(response.status_code, 400)
        attempt = LoginAttempt.objects.get(username="client")
        self.assertGreaterEqual(attempt.failed_attempts, 5)
        locked = self.client_api.post("/api/v1/auth/login/", {"username": "client", "password": "wrong"}, format="json")
        self.assertEqual(locked.status_code, 400)


class ReviewApiTests(ApiBaseTestCase):
    def test_review_can_be_left_only_once_for_order_item(self):
        past_date = self._previous_weekday(timezone.localdate())
        booking, order, line = self.create_booking(target_date=past_date)
        booking.start_time, booking.end_time = self._booking_datetimes(target_date=past_date, hour=12, minute=0, duration=55)
        booking.start_time -= timedelta(days=0)
        booking.end_time -= timedelta(days=0)
        booking.save()
        order.scheduled_for = booking.start_time
        order.save(update_fields=["scheduled_for"])
        self.auth_as_client()

        response = self.client_api.post(
            f"/api/v1/orders/{order.public_id}/reviews/",
            {"reservation_dish": line.public_id, "dish": self.dish1.id, "rating": 5, "comment": "Great"},
            format="json",
        )
        duplicate = self.client_api.post(
            f"/api/v1/orders/{order.public_id}/reviews/",
            {"reservation_dish": line.public_id, "dish": self.dish1.id, "rating": 4, "comment": "Again"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        self.assertTrue(OrderItemReview.objects.filter(order_item=line).exists())
