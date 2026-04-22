from datetime import datetime, timedelta
from decimal import Decimal

import pytz
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from bookings.models import (
    Dish,
    MenuOverride,
    MenuOverrideItem,
    News,
    Promotion,
    PromotionComboItem,
    Reservation,
    ReservationDish,
    Table,
    UserProfile,
    VenueComplaint,
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
        self.dish3 = Dish.objects.create(name="Tea", price=Decimal("50.00"), available_quantity=20)

        self.booking_date = self._next_weekday(timezone.localdate())
        self._ensure_weekly_menu(self.booking_date, [self.dish1, self.dish2])

    def _next_weekday(self, start_date, offset_days=1):
        candidate = start_date + timedelta(days=offset_days)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _ensure_weekly_menu(self, target_date, dishes):
        day_settings, _ = WeeklyMenuDaySettings.objects.get_or_create(
            day_of_week=target_date.weekday(),
            defaults={"is_active": True},
        )
        day_settings.is_active = True
        day_settings.save()
        WeeklyMenuItem.objects.filter(day_settings=day_settings).delete()
        for order, dish in enumerate(dishes, start=1):
            WeeklyMenuItem.objects.create(day_settings=day_settings, dish=dish, order=order)

    def _booking_datetimes(self, target_date=None, hour=12, minute=0, duration=55):
        target_date = target_date or self.booking_date
        start = MOSCOW_TZ.localize(datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute)))
        end = start + timedelta(minutes=duration)
        return start, end

    def auth_as_client(self):
        self.client_api.force_authenticate(user=self.client_user)

    def auth_as_other(self):
        self.client_api.force_authenticate(user=self.other_user)

    def auth_as_operator(self):
        self.client_api.force_authenticate(user=self.operator_user)

    def create_reservation(self, user=None, target_date=None, hour=12, minute=0, duration=55, table=None):
        user = user or self.client_user
        table = table or self.table2
        start, end = self._booking_datetimes(target_date, hour=hour, minute=minute, duration=duration)
        return Reservation.objects.create(
            user=user,
            table=table,
            guests_count=min(table.seats, 2),
            start_time=start,
            end_time=end,
            order_subtotal=Decimal("0"),
            order_total=Decimal("0"),
        )


class AuthApiTests(ApiBaseTestCase):
    def test_login_refresh_and_me(self):
        response = self.client_api.post("/api/v1/auth/login/", {"username": "client", "password": "pass12345"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        me_client = APIClient()
        me_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        me_response = me_client.get("/api/v1/auth/me/")
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.data["role"], "client")

        refresh_response = self.client_api.post("/api/v1/auth/refresh/", {"refresh": response.data["refresh"]}, format="json")
        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access", refresh_response.data)

    def test_login_fails_with_wrong_password(self):
        response = self.client_api.post("/api/v1/auth/login/", {"username": "client", "password": "wrong"}, format="json")
        self.assertEqual(response.status_code, 400)


class PublicApiTests(ApiBaseTestCase):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.news = News.objects.create(
            title="Published",
            summary="Summary",
            body="Body",
            is_published=True,
            published_at=now - timedelta(hours=1),
        )
        News.objects.create(
            title="Draft",
            summary="Hidden",
            body="Draft body",
            is_published=False,
            published_at=now,
        )
        self.promotion = Promotion.objects.create(
            name="Combo lunch",
            description="Combo",
            kind=Promotion.KIND_COMBO,
            discount_type=Promotion.DISCOUNT_FIXED_OFF,
            discount_value=Decimal("50.00"),
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            is_active=True,
        )
        PromotionComboItem.objects.create(promotion=self.promotion, dish=self.dish1, min_quantity=1)
        override = MenuOverride.objects.create(date_from=self.booking_date, is_active=True)
        MenuOverrideItem.objects.create(override=override, dish=self.dish3, action="add", order=1)

    def test_public_news_promotions_menu_and_dishes(self):
        news_response = self.client_api.get("/api/v1/news/")
        self.assertEqual(news_response.status_code, 200)
        self.assertEqual(news_response.data["count"], 1)

        promo_response = self.client_api.get("/api/v1/promotions/")
        self.assertEqual(promo_response.status_code, 200)
        self.assertEqual(promo_response.data["count"], 1)
        self.assertEqual(promo_response.data["results"][0]["combo_items"][0]["dish_id"], self.dish1.id)

        menu_response = self.client_api.get(f"/api/v1/menu/?date={self.booking_date.isoformat()}")
        self.assertEqual(menu_response.status_code, 200)
        self.assertIn(self.dish3.id, menu_response.data["dish_ids"])

        dishes_response = self.client_api.get(f"/api/v1/dishes/?date={self.booking_date.isoformat()}")
        self.assertEqual(dishes_response.status_code, 200)
        returned_ids = {item["id"] for item in dishes_response.data["results"]}
        self.assertIn(self.dish1.id, returned_ids)
        self.assertIn(self.dish3.id, returned_ids)

    def test_availability_endpoints(self):
        reservation = self.create_reservation()
        occupied = self.client_api.get(
            f"/api/v1/availability/occupied-slots/?table_id={self.table2.id}&date={self.booking_date.isoformat()}"
        )
        self.assertEqual(occupied.status_code, 200)
        self.assertEqual(len(occupied.data["occupied_slots"]), 1)

        available = self.client_api.get(
            f"/api/v1/availability/available-slots/?date={self.booking_date.isoformat()}&guests_count=2"
        )
        self.assertEqual(available.status_code, 200)
        self.assertIn("55", {str(key) for key in available.data["available_slots"].keys()})


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
        self.assertEqual(Reservation.objects.filter(user=self.client_user).count(), 1)

    def test_reservation_create_fails_when_slot_intersects(self):
        self.create_reservation(user=self.other_user, hour=12, minute=0, table=self.table2)
        self.create_reservation(user=self.other_user, hour=12, minute=0, table=self.table4)
        self.auth_as_client()
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

    def test_reservation_create_fails_when_guest_count_exceeds_capacity(self):
        self.auth_as_client()
        response = self.client_api.post(
            "/api/v1/reservations/",
            {
                "date": self.booking_date.isoformat(),
                "time": "12:00",
                "duration_minutes": 55,
                "guests_count": 5,
                "dishes": [{"dish": self.dish1.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_reservation_create_fails_outside_allowed_window(self):
        far_date = self.booking_date + timedelta(days=7)
        while far_date.weekday() >= 5:
            far_date += timedelta(days=1)
        self._ensure_weekly_menu(far_date, [self.dish1])
        self.auth_as_client()
        response = self.client_api.post(
            "/api/v1/reservations/",
            {
                "date": far_date.isoformat(),
                "time": "12:00",
                "duration_minutes": 55,
                "guests_count": 2,
                "dishes": [{"dish": self.dish1.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_user_cannot_access_foreign_reservation(self):
        reservation = self.create_reservation(user=self.other_user)
        self.auth_as_client()
        response = self.client_api.get(f"/api/v1/reservations/{reservation.id}/")
        self.assertEqual(response.status_code, 404)

    def test_operator_forbidden_on_client_endpoint(self):
        self.auth_as_operator()
        response = self.client_api.get("/api/v1/reservations/")
        self.assertEqual(response.status_code, 403)

    def test_cannot_delete_reservation_less_than_30_minutes_before_start(self):
        start = timezone.now() + timedelta(minutes=20)
        end = start + timedelta(minutes=55)
        reservation = Reservation.objects.create(
            user=self.client_user,
            table=self.table2,
            guests_count=2,
            start_time=start,
            end_time=end,
            order_subtotal=Decimal("0"),
            order_total=Decimal("0"),
        )
        self.auth_as_client()
        response = self.client_api.delete(f"/api/v1/reservations/{reservation.id}/")
        self.assertEqual(response.status_code, 400)


class ComplaintAndReviewApiTests(ApiBaseTestCase):
    def test_client_sees_only_own_complaints(self):
        VenueComplaint.objects.create(user=self.client_user, subject="Mine", message="A")
        VenueComplaint.objects.create(user=self.other_user, subject="Other", message="B")
        self.auth_as_client()
        response = self.client_api.get("/api/v1/complaints/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["subject"], "Mine")

    def test_review_can_be_left_only_for_own_line_and_duplicate_is_blocked(self):
        start = timezone.now() - timedelta(hours=2)
        end = timezone.now() - timedelta(hours=1)
        reservation = Reservation.objects.create(
            user=self.client_user,
            table=self.table2,
            guests_count=2,
            start_time=start,
            end_time=end,
            order_subtotal=Decimal("120.00"),
            order_total=Decimal("120.00"),
        )
        line = ReservationDish.objects.create(reservation=reservation, dish=self.dish1, quantity=1)
        self.auth_as_client()

        response = self.client_api.post(
            f"/api/v1/orders/{reservation.id}/reviews/",
            {"reservation_dish": line.id, "dish": self.dish1.id, "rating": 5, "comment": "Great"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        duplicate = self.client_api.post(
            f"/api/v1/orders/{reservation.id}/reviews/",
            {"reservation_dish": line.id, "dish": self.dish1.id, "rating": 4, "comment": "Again"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)

    def test_html_routes_still_work(self):
        self.client.login(username="client", password="pass12345")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
