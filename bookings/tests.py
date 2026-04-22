from django.test import TestCase, Client
from django.contrib.auth.models import User

from bookings.models import UserProfile


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
