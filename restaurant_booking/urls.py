"""restaurant_booking URL Configuration"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import include, path

from bookings.views_auth import CustomLoginView
from bookings.views_booking import reservation_create


def home(request):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())

    if request.user.is_superuser:
        return redirect("admin_cabinet")

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    if profile and profile.role == "operator":
        return redirect("operator_cabinet")
    if profile and profile.role == "admin":
        return redirect("admin_cabinet")
    return reservation_create(request)


urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("bookings.api.urls")),
    path("accounts/login/", CustomLoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(next_page="/"), name="logout"),
    path("dashboard/", include("bookings.urls")),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
