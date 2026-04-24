from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone

from bookings.models import Booking, CustomerOrder, Dish, OrderAppliedPromotion, OrderItem, Promotion, Table, UserProfile, VenueComplaint, WeeklyMenuDaySettings, WeeklyMenuItem


class AdminCabinetAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user('admintest', password='testpass123')
        UserProfile.objects.create(user=self.admin_user, role='admin')
        self.client_user = User.objects.create_user('clienttest', password='testpass123')
        UserProfile.objects.create(user=self.client_user, role='client')

    def test_admin_cabinet_ok_for_admin_role(self):
        self.client.login(username='admintest', password='testpass123')
        response = self.client.get('/dashboard/admin/cabinet/')
        self.assertEqual(response.status_code, 200)

    def test_admin_cabinet_forbidden_for_client(self):
        self.client.login(username='clienttest', password='testpass123')
        response = self.client.get('/dashboard/admin/cabinet/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)

    def test_admin_cabinet_redirects_anonymous(self):
        response = self.client.get('/dashboard/admin/cabinet/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)


class ClientNavigationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.client_user = User.objects.create_user('clientmenu', password='testpass123')
        UserProfile.objects.create(user=self.client_user, role='client')
        self.dish = Dish.objects.create(name='Лосось в сливочном соусе', price=Decimal('920.00'), available_quantity=10)
        today = timezone.localdate()
        self.day_settings = WeeklyMenuDaySettings.objects.create(day_of_week=today.weekday(), is_active=True)
        WeeklyMenuItem.objects.create(day_settings=self.day_settings, dish=self.dish, order=1)
        Promotion.objects.create(
            name='-10% на блюда из рыбы',
            description='Скидка на рыбу',
            kind=Promotion.KIND_SINGLE,
            discount_type=Promotion.DISCOUNT_PERCENT,
            discount_value=Decimal('10.00'),
            valid_from=timezone.now() - timedelta(days=1),
            valid_to=timezone.now() + timedelta(days=1),
            is_active=True,
            target_dish=self.dish,
        )

    def test_client_today_menu_page_is_available(self):
        self.client.login(username='clientmenu', password='testpass123')
        response = self.client.get('/dashboard/client/menu/today/')
        self.assertEqual(response.status_code, 200)

    def test_home_does_not_render_today_menu_block(self):
        self.client.login(username='clientmenu', password='testpass123')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'У каждого блюда можно открыть отзывы.')

    def test_home_hides_regular_quantity_for_promo_dish(self):
        self.client.login(username='clientmenu', password='testpass123')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'name="dish_quantity_{self.dish.pk}"', html=False)


class ClientOrderAndReviewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('clientreview', password='testpass123')
        UserProfile.objects.create(user=self.user, role='client')
        self.table = Table.objects.create(table_number='T7', seats=4)
        self.dish = Dish.objects.create(name='Греческий салат', price=Decimal('250.00'), available_quantity=20)
        start_time = timezone.now() - timedelta(hours=2)
        end_time = timezone.now() - timedelta(hours=1)
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
            subtotal_amount=Decimal('250.00'),
            discount_total=Decimal('50.00'),
            total_amount=Decimal('200.00'),
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
            name='Салат со скидкой',
            description='Тестовая акция',
            kind=Promotion.KIND_SINGLE,
            discount_type=Promotion.DISCOUNT_FIXED_OFF,
            discount_value=Decimal('50.00'),
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
            original_amount_snapshot=Decimal('250.00'),
            discount_amount_snapshot=Decimal('50.00'),
        )

    def test_order_detail_shows_line_prices(self):
        self.client.login(username='clientreview', password='testpass123')
        response = self.client.get(f'/dashboard/client/orders/{self.order.public_id}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Греческий салат')
        self.assertContains(response, '250')
        self.assertContains(response, '200')

    def test_dish_review_list_shows_leave_review_link(self):
        self.client.login(username='clientreview', password='testpass123')
        response = self.client.get(f'/dashboard/client/dishes/{self.dish.pk}/reviews/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Заказ №{self.order.public_id}')


class OperatorComplaintTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.operator = User.objects.create_user('operatorlabels', password='testpass123')
        self.customer = User.objects.create_user('customerlabels', password='testpass123')
        UserProfile.objects.create(user=self.operator, role='operator')
        UserProfile.objects.create(user=self.customer, role='client')
        VenueComplaint.objects.create(user=self.customer, subject='Тест', message='Сообщение', status='new')

    def test_operator_complaints_show_russian_statuses(self):
        self.client.login(username='operatorlabels', password='testpass123')
        response = self.client.get('/dashboard/operator/complaints/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новая')
        self.assertContains(response, 'Просмотрена')
        self.assertContains(response, 'Закрыта')
