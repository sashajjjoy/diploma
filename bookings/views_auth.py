from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from bookings.models import UserProfile

class CustomLoginView(LoginView):
    """Кастомный view для логина с редиректом в зависимости от роли"""
    
    def get_success_url(self):
        """Редирект после успешного входа в зависимости от роли пользователя"""
        user = self.request.user
        
        try:
            profile = user.profile
            if profile.role == 'operator':
                return '/dashboard/operator/'
            elif profile.role == 'admin' or user.is_superuser:
                return '/admin/'
            else:
                # Клиент или роль не определена
                return '/'
        except UserProfile.DoesNotExist:
            # Если профиля нет, создаем его с ролью клиента
            UserProfile.objects.create(user=user, role='client')
            return '/'

