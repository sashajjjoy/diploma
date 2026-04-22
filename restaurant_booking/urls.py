"""restaurant_booking URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import render, redirect
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from bookings.views_auth import CustomLoginView


def home(request):
    """Главная страница с информацией о проекте"""
    from bookings.views import reservation_create
    from bookings.models import Reservation
    
    # Если пользователь не авторизован - редирект на логин
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    if request.user.is_superuser:
        return redirect('admin_cabinet')

    # Если пользователь - оператор или администратор — в соответствующий кабинет
    try:
        profile = request.user.profile
        if profile.role == 'operator':
            return redirect('operator_cabinet')
        if profile.role == 'admin':
            return redirect('admin_cabinet')
    except Exception:
        pass
    
    # Если это POST запрос от клиента, обрабатываем форму
    if request.method == 'POST':
        try:
            profile = request.user.profile
            if profile.role == 'client':
                return reservation_create(request)
        except:
            pass
    
    # Для GET запроса - показываем главную для клиентов
    form = None
    
    # Получаем бронирования пользователя, если он авторизован
    reservations = []
    reservations_page = None
    available_dates = []
    time_slots = []
    
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.role == 'client':
                from django.core.paginator import Paginator
                reservations_query = Reservation.objects.filter(user=request.user).order_by('-start_time')
                paginator = Paginator(reservations_query, 10)
                page_number = request.GET.get('page', 1)
                reservations_page = paginator.get_page(page_number)
                reservations = reservations_page
                
                # Генерируем доступные рабочие дни (используем московское время)
                from datetime import timedelta
                from django.utils import timezone
                from django.utils.formats import date_format
                now = timezone.localtime(timezone.now())
                
                # Сегодня
                if now.weekday() < 5:  # Понедельник-пятница
                    available_dates.append(('today', 'Сегодня', now.date()))
                
                # Завтра
                tomorrow = (now + timedelta(days=1)).date()
                if tomorrow.weekday() < 5:
                    available_dates.append(('tomorrow', 'Завтра', tomorrow))
                
                # Послезавтра (только рабочие дни)
                day_after = (now + timedelta(days=2)).date()
                # Проверяем, является ли послезавтра рабочим днем, если нет - находим ближайший рабочий день
                while day_after.weekday() >= 5:  # Пропускаем выходные
                    day_after = day_after + timedelta(days=1)
                # Проверяем, что не превышаем лимит в 2 рабочих дня
                working_days = 0
                check_date = now.date()
                while check_date < day_after:
                    if check_date.weekday() < 5:
                        working_days += 1
                    check_date = check_date + timedelta(days=1)
                if working_days <= 2:  # Только если не более 2 рабочих дней
                    available_dates.append(('day_after_tomorrow', date_format(day_after, "d F"), day_after))
                
                # Генерируем доступные временные слоты
                for hour in range(12, 22):  # С 12:00 до 21:30
                    for minute in [0, 30]:
                        time_slots.append(f"{hour:02d}:{minute:02d}")
                
                # Получаем доступные блюда для предзаказа
                from bookings.models import Dish
                from bookings.views import get_menu_dishes_for_date, client_home_promotion_context
                import json
                all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by('name')
                
                # Получаем блюда в меню для каждой доступной даты
                dishes_by_date = {}
                for date_key, date_label, date_obj in available_dates:
                    dishes_by_date[date_key] = list(get_menu_dishes_for_date(date_obj))
                dishes_by_date_json = json.dumps(dishes_by_date)
                promo_ctx = client_home_promotion_context()
            else:
                all_dishes = []
                promo_ctx = {}
        except:
            all_dishes = []
            promo_ctx = {}
            pass
    
    context = {
        'reservations': reservations,
        'available_dates': available_dates,
        'time_slots': time_slots,
        'all_dishes': all_dishes if 'all_dishes' in locals() else [],
        'dishes_by_date': dishes_by_date_json if 'dishes_by_date_json' in locals() else '{}',
        'page_obj': reservations_page,
        **(promo_ctx if 'promo_ctx' in locals() and promo_ctx else {
            'active_promotions': [],
            'combo_promotions': [],
            'single_promos_by_dish': {},
        }),
    }
    return render(request, 'home.html', context)


urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    
    # Аутентификация
    path('accounts/login/', CustomLoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(next_page='/'), name='logout'),
    
    # Личные кабинеты
    path('dashboard/', include('bookings.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
