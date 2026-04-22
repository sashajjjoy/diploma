from django.contrib.auth.views import LoginView
from django.urls import reverse
from bookings.models import UserProfile


class CustomLoginView(LoginView):
    """Кастомный view для логина с редиректом в зависимости от роли"""
    
    def get_success_url(self):
        """Редирект после успешного входа в зависимости от роли пользователя"""
        user = self.request.user
        
        if user.is_superuser:
            return reverse('admin_cabinet')
        try:
            profile = user.profile
            if profile.role == 'operator':
                return '/dashboard/operator/'
            if profile.role == 'admin':
                return reverse('admin_cabinet')
            return '/'
        except UserProfile.DoesNotExist:
            # Если профиля нет, создаем его с ролью клиента
            UserProfile.objects.create(user=user, role='client')
            return '/'

