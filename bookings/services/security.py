from datetime import datetime, timedelta

from django.contrib.auth import logout
from django.utils import timezone

from bookings.models import LoginAttempt, SecuritySettings


SESSION_ACTIVITY_KEY = "last_activity_ts"


def get_security_settings():
    return SecuritySettings.get_solo()


def get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_or_create_login_attempt(username):
    return LoginAttempt.objects.get_or_create(username=username or "", defaults={"failed_attempts": 0})[0]


def is_login_locked(username):
    settings = get_security_settings()
    if not settings.lockout_enabled or not username:
        return False, None
    attempt = LoginAttempt.objects.filter(username=username).first()
    if attempt and attempt.is_locked:
        return True, attempt
    return False, attempt


def record_failed_login(username, request=None):
    if not username:
        return None
    settings = get_security_settings()
    attempt = get_or_create_login_attempt(username)
    attempt.failed_attempts += 1
    attempt.last_failed_at = timezone.now()
    if request is not None:
        attempt.last_ip = get_client_ip(request)
    if settings.lockout_enabled and attempt.failed_attempts >= settings.max_failed_login_attempts:
        attempt.locked_until = timezone.now() + timedelta(minutes=settings.login_lockout_minutes)
    attempt.save(update_fields=["failed_attempts", "last_failed_at", "last_ip", "locked_until"])
    return attempt


def clear_login_attempt(username):
    if not username:
        return
    LoginAttempt.objects.filter(username=username).update(
        failed_attempts=0,
        locked_until=None,
        last_failed_at=None,
    )


def unlock_login_attempt(attempt):
    attempt.failed_attempts = 0
    attempt.locked_until = None
    attempt.save(update_fields=["failed_attempts", "locked_until"])


def session_expired(request):
    settings = get_security_settings()
    last_activity_raw = request.session.get(SESSION_ACTIVITY_KEY)
    if not last_activity_raw:
        return False
    try:
        last_activity = datetime.fromisoformat(last_activity_raw)
        if timezone.is_naive(last_activity):
            last_activity = timezone.make_aware(last_activity, timezone.get_current_timezone())
    except Exception:
        return False
    return timezone.now() > last_activity + timedelta(minutes=settings.session_timeout_minutes)


def touch_session(request):
    request.session[SESSION_ACTIVITY_KEY] = timezone.now().isoformat()


def logout_for_idle_timeout(request):
    logout(request)
    request.session.flush()
