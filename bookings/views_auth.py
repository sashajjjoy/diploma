from django.contrib.auth.views import LoginView
from django.utils import timezone
from django.urls import reverse

from bookings.models import UserProfile
from bookings.services.security import clear_login_attempt, is_login_locked, record_failed_login


class CustomLoginView(LoginView):
    def dispatch(self, request, *args, **kwargs):
        self._skip_failed_login_record = False
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "").strip()
        is_locked, attempt = is_login_locked(username)
        if is_locked and attempt is not None:
            form = self.get_form()
            seconds_left = max(0, int((attempt.locked_until - timezone.now()).total_seconds()))
            minutes_left = max(1, seconds_left // 60 or 1)
            form.add_error(None, f"Вход временно заблокирован. Повторите попытку примерно через {minutes_left} мин.")
            self._skip_failed_login_record = True
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        clear_login_attempt(form.cleaned_data.get("username", "").strip())
        return super().form_valid(form)

    def form_invalid(self, form):
        username = self.request.POST.get("username", "").strip()
        if username and not getattr(self, "_skip_failed_login_record", False):
            record_failed_login(username, self.request)
        return super().form_invalid(form)

    def get_success_url(self):
        user = self.request.user
        if user.is_superuser:
            return reverse("admin_cabinet")
        try:
            profile = user.profile
            if profile.role == UserProfile.ROLE_OPERATOR:
                return reverse("operator_cabinet")
            if profile.role == UserProfile.ROLE_ADMIN:
                return reverse("admin_cabinet")
            return "/"
        except UserProfile.DoesNotExist:
            UserProfile.objects.create(user=user, role=UserProfile.ROLE_CLIENT)
            return "/"
