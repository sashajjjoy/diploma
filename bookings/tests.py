from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from bookings.models import (
    BackupArchive,
    Booking,
    CustomerOrder,
    Dish,
    ExternalIntegration,
    LoginAttempt,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    Promotion,
    ServiceDurationOption,
    ServiceSlotSettings,
    ServiceWeekdayWindow,
    Table,
    UserProfile,
    VenueComplaint,
    WeeklyMenuDaySettings,
    WeeklyMenuItem,
)


def previous_weekday(start_date):
    candidate = start_date - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


class AdminCabinetAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user("admintest", password="testpass123")
        self.client_user = User.objects.create_user("clienttest", password="testpass123")
        self.operator_user = User.objects.create_user("operatortest", password="testpass123")
        UserProfile.objects.create(user=self.admin_user, role=UserProfile.ROLE_ADMIN)
        UserProfile.objects.create(user=self.client_user, role=UserProfile.ROLE_CLIENT)
        UserProfile.objects.create(user=self.operator_user, role=UserProfile.ROLE_OPERATOR)

    def test_admin_cabinet_ok_for_admin_role(self):
        self.client.login(username="admintest", password="testpass123")
        response = self.client.get("/dashboard/admin/cabinet/")
        self.assertEqual(response.status_code, 200)

    def test_admin_cabinet_forbidden_for_client(self):
        self.client.login(username="clienttest", password="testpass123")
        response = self.client.get("/dashboard/admin/cabinet/")
        self.assertEqual(response.status_code, 302)

    def test_admin_cabinet_redirects_anonymous(self):
        response = self.client.get("/dashboard/admin/cabinet/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_admin_cabinet_shows_roles_and_user_management(self):
        self.client.login(username="admintest", password="testpass123")
        response = self.client.get("/dashboard/admin/cabinet/")
        self.assertContains(response, "Клиенты")
        self.assertContains(response, "Операторы")
        self.assertContains(response, "Логин")

    def test_admin_can_update_username_and_role(self):
        self.client.login(username="admintest", password="testpass123")
        response = self.client.post(
            "/dashboard/admin/cabinet/",
            {
                "user_id": self.client_user.id,
                "username": "client_renamed",
                "first_name": "Новый",
                "last_name": "Клиент",
                "email": "client@example.com",
                "phone": "+79990000000",
                "role": UserProfile.ROLE_OPERATOR,
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.username, "client_renamed")
        self.assertEqual(self.client_user.profile.role, UserProfile.ROLE_OPERATOR)


class ClientNavigationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client_user = User.objects.create_user("clientmenu", password="testpass123")
        UserProfile.objects.create(user=self.client_user, role=UserProfile.ROLE_CLIENT)
        self.dish = Dish.objects.create(name="Лосось в сливочном соусе", price=Decimal("920.00"), available_quantity=10)
        today = timezone.localdate()
        self.day_settings = WeeklyMenuDaySettings.objects.create(day_of_week=today.weekday(), is_active=True)
        WeeklyMenuItem.objects.create(day_settings=self.day_settings, dish=self.dish, order=1)
        Promotion.objects.create(
            name="-10% на блюда из рыбы",
            description="Скидка на рыбу",
            kind=Promotion.KIND_SINGLE,
            discount_type=Promotion.DISCOUNT_PERCENT,
            discount_value=Decimal("10.00"),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
            target_dish=self.dish,
        )

    def test_client_today_menu_page_is_available(self):
        self.client.login(username="clientmenu", password="testpass123")
        response = self.client.get("/dashboard/client/menu/today/")
        self.assertEqual(response.status_code, 200)

    def test_home_does_not_render_today_menu_block(self):
        self.client.login(username="clientmenu", password="testpass123")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "У каждого блюда можно открыть отзывы.")

    def test_home_hides_regular_quantity_for_promo_dish(self):
        self.client.login(username="clientmenu", password="testpass123")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'name="dish_quantity_{self.dish.pk}"', html=False)


class ClientOrderAndReviewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("clientreview", password="testpass123")
        UserProfile.objects.create(user=self.user, role=UserProfile.ROLE_CLIENT)
        self.table = Table.objects.create(table_number="T7", seats=4)
        self.dish = Dish.objects.create(name="Греческий салат", price=Decimal("250.00"), available_quantity=20)
        target_date = previous_weekday(timezone.localdate())
        start_time = timezone.make_aware(datetime.combine(target_date, datetime.min.time().replace(hour=12, minute=0)))
        end_time = start_time + timedelta(hours=1)
        self.booking = Booking.objects.create(
            user=self.user,
            table=self.table,
            guests_count=2,
            start_time=start_time,
            end_time=end_time,
        )
        self.order = CustomerOrder.objects.create(
            public_id=self.booking.public_id,
            user=self.user,
            booking=self.booking,
            order_type=CustomerOrder.TYPE_DINE_IN,
            scheduled_for=start_time,
            status=CustomerOrder.STATUS_COMPLETED,
            subtotal_amount=Decimal("250.00"),
            discount_total=Decimal("50.00"),
            total_amount=Decimal("200.00"),
        )
        self.line = OrderItem.objects.create(
            order=self.order,
            dish=self.dish,
            dish_name_snapshot=self.dish.name,
            unit_price_snapshot=self.dish.price,
            quantity=1,
            line_total_snapshot=self.dish.price,
        )
        self.promotion = Promotion.objects.create(
            name="Салат со скидкой",
            description="Тестовая акция",
            kind=Promotion.KIND_SINGLE,
            discount_type=Promotion.DISCOUNT_FIXED_OFF,
            discount_value=Decimal("50.00"),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
            target_dish=self.dish,
        )
        OrderAppliedPromotion.objects.create(
            order=self.order,
            promotion=self.promotion,
            promotion_name_snapshot=self.promotion.name,
            quantity_applied=1,
            original_amount_snapshot=Decimal("250.00"),
            discount_amount_snapshot=Decimal("50.00"),
        )

    def test_order_detail_shows_line_prices(self):
        self.client.login(username="clientreview", password="testpass123")
        response = self.client.get(f"/dashboard/client/orders/{self.order.public_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Греческий салат")
        self.assertContains(response, "250")
        self.assertContains(response, "200")

    def test_dish_review_list_shows_leave_review_link(self):
        self.client.login(username="clientreview", password="testpass123")
        response = self.client.get(f"/dashboard/client/dishes/{self.dish.pk}/reviews/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Заказ №{self.order.public_id}")


class OperatorComplaintTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user("operatorlabels", password="testpass123")
        self.customer = User.objects.create_user("customerlabels", password="testpass123")
        UserProfile.objects.create(user=self.operator, role=UserProfile.ROLE_OPERATOR)
        UserProfile.objects.create(user=self.customer, role=UserProfile.ROLE_CLIENT)
        VenueComplaint.objects.create(user=self.customer, subject="Тест", message="Сообщение", status="new")
        self.table = Table.objects.create(table_number="T1", seats=2)
        self.dish = Dish.objects.create(name="Борщ", price=Decimal("180.00"), available_quantity=10)
        target_date = previous_weekday(timezone.localdate())
        start_time = timezone.make_aware(datetime.combine(target_date, datetime.min.time().replace(hour=12, minute=0)))
        end_time = start_time + timedelta(hours=1)
        booking = Booking.objects.create(
            user=self.customer,
            table=self.table,
            guests_count=2,
            start_time=start_time,
            end_time=end_time,
        )
        order = CustomerOrder.objects.create(
            public_id=booking.public_id,
            user=self.customer,
            booking=booking,
            order_type=CustomerOrder.TYPE_DINE_IN,
            scheduled_for=booking.start_time,
            status=CustomerOrder.STATUS_COMPLETED,
            subtotal_amount=Decimal("180.00"),
            total_amount=Decimal("180.00"),
        )
        item = OrderItem.objects.create(
            order=order,
            dish=self.dish,
            dish_name_snapshot=self.dish.name,
            unit_price_snapshot=self.dish.price,
            quantity=1,
            line_total_snapshot=self.dish.price,
        )
        OrderItemReview.objects.create(order_item=item, rating=5, comment="Отлично")

    def test_operator_complaints_show_russian_statuses(self):
        self.client.login(username="operatorlabels", password="testpass123")
        response = self.client.get("/dashboard/operator/complaints/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новая")
        self.assertContains(response, "Просмотрена")
        self.assertContains(response, "Закрыта")

    def test_operator_home_shows_complaints_and_reviews(self):
        self.client.login(username="operatorlabels", password="testpass123")
        response = self.client.get("/dashboard/operator/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Последние жалобы клиентов")
        self.assertContains(response, "Последние отзывы о блюдах")
        self.assertContains(response, "Борщ")


    def test_operator_dish_detail_page_opens(self):
        self.client.login(username="operatorlabels", password="testpass123")
        response = self.client.get(f"/dashboard/operator/dishes/{self.dish.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Р‘РѕСЂС‰")


class RoleBoundaryAndFeatureTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user("roleadmin", password="testpass123", email="admin@example.com")
        self.operator_user = User.objects.create_user("roleoperator", password="testpass123")
        self.client_user = User.objects.create_user("roleclient", password="testpass123")
        UserProfile.objects.create(user=self.admin_user, role=UserProfile.ROLE_ADMIN, phone="+79990000000")
        UserProfile.objects.create(user=self.operator_user, role=UserProfile.ROLE_OPERATOR)
        UserProfile.objects.create(user=self.client_user, role=UserProfile.ROLE_CLIENT)
        ServiceWeekdayWindow.ensure_defaults()
        ServiceDurationOption.ensure_defaults()

    def test_admin_cannot_open_operator_dashboard(self):
        self.client.login(username="roleadmin", password="testpass123")
        response = self.client.get("/dashboard/operator/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], "/dashboard/admin/cabinet/")

    def test_operator_cannot_open_admin_pages(self):
        self.client.login(username="roleoperator", password="testpass123")
        response = self.client.get("/dashboard/admin/integrations/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], "/dashboard/operator/")

    def test_admin_cabinet_shows_russian_roles_and_phone(self):
        self.client.login(username="roleadmin", password="testpass123")
        response = self.client.get("/dashboard/admin/cabinet/")
        self.assertContains(response, "Клиент")
        self.assertContains(response, "Оператор")
        self.assertContains(response, "Администратор")
        self.assertContains(response, "Телефон")

    def test_last_admin_cannot_demote_self(self):
        self.client.login(username="roleadmin", password="testpass123")
        response = self.client.post(
            "/dashboard/admin/cabinet/",
            {
                "user_id": self.admin_user.id,
                "username": self.admin_user.username,
                "first_name": "",
                "last_name": "",
                "email": self.admin_user.email,
                "phone": "+79990000000",
                "role": UserProfile.ROLE_CLIENT,
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.admin_user.refresh_from_db()
        self.assertEqual(self.admin_user.profile.role, UserProfile.ROLE_ADMIN)

    @patch("bookings.views_booking.check_external_integration", return_value=(True, "ok"))
    def test_admin_can_test_integration(self, mocked_check):
        integration = ExternalIntegration.objects.create(name="ERP", base_url="https://example.com/api")
        self.client.login(username="roleadmin", password="testpass123")
        response = self.client.get(f"/dashboard/admin/integrations/{integration.pk}/test/", follow=True)
        self.assertEqual(response.status_code, 200)
        mocked_check.assert_called_once()

    def test_admin_pages_render(self):
        BackupArchive.objects.create(original_name="test.json.gz", file="backups/test.json.gz", created_by=self.admin_user)
        LoginAttempt.objects.create(username="blocked", failed_attempts=2)
        self.client.login(username="roleadmin", password="testpass123")
        for path in (
            "/dashboard/admin/integrations/",
            "/dashboard/admin/security/",
            "/dashboard/admin/backups/",
            "/dashboard/admin/reports/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_operator_report_page_and_csv(self):
        self.client.login(username="roleoperator", password="testpass123")
        html = self.client.get("/dashboard/operator/reports/?report=bookings")
        csv = self.client.get("/dashboard/operator/reports/?report=bookings&export=csv")
        self.assertEqual(html.status_code, 200)
        self.assertEqual(csv.status_code, 200)
        self.assertIn("text/csv", csv["Content-Type"])

    def test_operator_can_change_slot_settings(self):
        self.client.login(username="roleoperator", password="testpass123")
        monday = ServiceWeekdayWindow.objects.get(weekday=0)
        response = self.client.post(
            "/dashboard/operator/service-slots/",
            {
                "booking_lead_time_minutes": "45",
                "max_working_days_ahead": "3",
                "slot_step_minutes": "15",
                "duration_values": "40, 70",
                "window_active_0": "on",
                "window_open_0": monday.open_time.strftime("%H:%M"),
                "window_close_0": monday.close_time.strftime("%H:%M"),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ServiceSlotSettings.get_solo().booking_lead_time_minutes, 45)
        self.assertEqual(
            list(ServiceDurationOption.objects.filter(is_active=True).values_list("duration_minutes", flat=True)),
            [40, 70],
        )
