from django.contrib import messages
from django.shortcuts import redirect

from bookings.services.security import logout_for_idle_timeout, session_expired, touch_session


class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            if session_expired(request):
                logout_for_idle_timeout(request)
                messages.info(request, "Сессия завершена из-за бездействия.")
                return redirect("login")
            touch_session(request)
        return self.get_response(request)
