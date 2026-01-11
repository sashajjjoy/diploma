from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q
from django.core.paginator import Paginator
from datetime import timedelta, datetime
import pytz
from .models import Table, Dish, Reservation, ReservationDish, UserProfile, WeeklyMenu, WeeklyMenuItem, MenuOverride, MenuOverrideItem
from .forms import ReservationForm

MOSCOW_TZ = pytz.timezone('Europe/Moscow')


def get_menu_dishes_for_date(target_date):
    """
    Получает список блюд, доступных в меню на указанную дату.
    Учитывает еженедельное меню и переопределения.
    
    Args:
        target_date: date объект
        
    Returns:
        set: множество ID блюд, доступных в меню на эту дату
    """
    day_of_week = target_date.weekday()  # 0=понедельник, 6=воскресенье
    
    # Получаем еженедельное меню для этого дня недели
    weekly_dishes = []
    try:
        weekly_menu = WeeklyMenu.objects.get(day_of_week=day_of_week, is_active=True)
        weekly_menu_items = WeeklyMenuItem.objects.filter(menu=weekly_menu).order_by('order', 'dish__name')
        weekly_dishes = [item.dish for item in weekly_menu_items]
    except WeeklyMenu.DoesNotExist:
        pass
    
    # Получаем активные переопределения для этой даты
    active_overrides = MenuOverride.objects.filter(
        is_active=True,
        date_from__lte=target_date
    ).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=target_date)
    ).order_by('-date_from')
    
    # Применяем переопределения
    final_dishes = list(weekly_dishes)  # Начинаем с еженедельного меню
    dish_set = set(d.id for d in final_dishes)
    
    for override in active_overrides:
        override_items = MenuOverrideItem.objects.filter(override=override).order_by('order', 'dish__name')
        for item in override_items:
            if item.action == 'add':
                if item.dish.id not in dish_set:
                    final_dishes.append(item.dish)
                    dish_set.add(item.dish.id)
            elif item.action == 'remove':
                final_dishes = [d for d in final_dishes if d.id != item.dish.id]
                dish_set.discard(item.dish.id)
    
    return dish_set


def find_available_table(guests_count, start_datetime, end_datetime, exclude_reservation_id=None):
    """
    Автоматически выбирает подходящий столик для бронирования.
    Выбирает столик с наименьшим достаточным количеством мест из доступных.
    
    Args:
        guests_count: Количество гостей
        start_datetime: Начало бронирования (aware datetime)
        end_datetime: Окончание бронирования (aware datetime)
        exclude_reservation_id: ID бронирования для исключения (при редактировании)
    
    Returns:
        Table или None, если подходящий столик не найден
    """
    # Получаем все столики с достаточным количеством мест, отсортированные по количеству мест (от меньшего к большему)
    suitable_tables = Table.objects.filter(seats__gte=guests_count).order_by('seats')
    
    for table in suitable_tables:
        # Проверяем, нет ли пересекающихся бронирований для этого столика
        overlapping_query = Reservation.objects.filter(
            table=table
        ).filter(
            Q(start_time__lt=end_datetime) & Q(end_time__gt=start_datetime)
        )
        
        # Исключаем текущее бронирование при редактировании
        if exclude_reservation_id:
            overlapping_query = overlapping_query.exclude(pk=exclude_reservation_id)
        
        # Если пересечений нет, этот столик подходит
        if not overlapping_query.exists():
            return table
    
    # Не найден подходящий столик
    return None

def is_client(user):
    if not user.is_authenticated:
        return False
    try:
        profile = user.profile
        return profile.role == 'client'
    except UserProfile.DoesNotExist:
        return False

def is_operator(user):
    if not user.is_authenticated:
        return False
    try:
        profile = user.profile
        return profile.role == 'operator'
    except UserProfile.DoesNotExist:
        return False

@login_required
def dashboard(request):
    try:
        profile = request.user.profile
        if profile.is_client():
            return redirect('home')
        elif profile.is_operator():
            return redirect('operator_cabinet')
        elif profile.is_admin() or request.user.is_superuser:
            return redirect('/admin/')
    except (UserProfile.DoesNotExist, AttributeError):
        # Если профиля нет, создаем его автоматически с ролью клиента
        UserProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': 'client'}
        )
        # Создаем UserProfile, если его нет
        UserProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': 'client'}
        )
    return redirect('home')

def reservation_create(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Необходимо войти в систему для создания бронирования.')
        return redirect('login')
    
    # Проверяем роль
    try:
        profile = request.user.profile
        if profile.role != 'client':
            messages.error(request, 'Бронирование доступно только клиентам.')
            return redirect('home')
    except (UserProfile.DoesNotExist, AttributeError):
        UserProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': 'client'}
        )
    
    # Создаем UserProfile, если его нет
    UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'client'}
    )
    
    if request.method == 'POST':
        # Проверяем, это заказ на вынос или бронирование столика
        is_takeout = request.POST.get('takeout') == 'on'
        
        if is_takeout:
            # Обрабатываем заказ на вынос - создаем Reservation без столика на целый день
            try:
                # Получаем выбранную дату для takeout
                date = request.POST.get('takeout_date')  # 'today', 'tomorrow', 'day_after_tomorrow'
                from datetime import datetime
                now = timezone.localtime(timezone.now())
                
                if date == 'today':
                    start_date = now.date()
                elif date == 'tomorrow':
                    start_date = (now + timedelta(days=1)).date()
                elif date == 'day_after_tomorrow':
                    start_date = (now + timedelta(days=2)).date()
                    while start_date.weekday() >= 5:
                        start_date = start_date + timedelta(days=1)
                else:
                    messages.error(request, 'Выберите дату заказа.')
                    return redirect('home')
                
                # Создаем бронирование на целый день (00:00 - 23:59)
                start_datetime = MOSCOW_TZ.localize(datetime.combine(start_date, datetime.min.time().replace(hour=0, minute=0)))
                end_datetime = MOSCOW_TZ.localize(datetime.combine(start_date, datetime.min.time().replace(hour=23, minute=59)))
                
                # Создаем Reservation без столика
                reservation = Reservation(
                    user=request.user,
                    table=None,
                    guests_count=1,  # Для заказа на вынос не важно
                    start_time=start_datetime,
                    end_time=end_datetime
                )
                reservation.full_clean()
                reservation.save()
                
                # Обрабатываем блюда из формы
                from django.db.models import Sum
                dish_errors = []
                has_dishes = False
                
                for key, value in request.POST.items():
                    if key.startswith('dish_quantity_'):
                        dish_id = int(key.replace('dish_quantity_', ''))
                        quantity = int(value) if value else 0
                        
                        if quantity > 0:
                            has_dishes = True
                            try:
                                dish = Dish.objects.get(pk=dish_id)
                                
                                # Проверка доступности (учитываем все резервации, включая заказы на вынос)
                                reserved_result = ReservationDish.objects.filter(
                                    dish=dish,
                                    reservation__end_time__gte=timezone.now()
                                ).aggregate(total=Sum('quantity'))
                                reserved = reserved_result['total'] or 0
                                available = dish.available_quantity - reserved
                                
                                if quantity > available:
                                    dish_errors.append(f'Блюдо "{dish.name}": недостаточно. Доступно: {available}')
                                else:
                                    ReservationDish.objects.create(
                                        reservation=reservation,
                                        dish=dish,
                                        quantity=quantity
                                    )
                            except (Dish.DoesNotExist, ValueError, ValidationError) as e:
                                dish_errors.append(f'Ошибка при добавлении блюда: {str(e)}')
                
                # Проверяем, что выбрано хотя бы одно блюдо
                if not has_dishes:
                    reservation.delete()  # Удаляем бронирование
                    messages.error(request, 'Для заказа на вынос необходимо выбрать хотя бы одно блюдо.')
                    return redirect('home')
                
                if dish_errors:
                    reservation.delete()  # Удаляем бронирование, если были ошибки
                    messages.error(request, 'Ошибки при создании заказа: ' + '; '.join(dish_errors))
                    return redirect('home')
                else:
                    messages.success(request, f'Заказ на вынос на {start_date.strftime("%d.%m.%Y")} успешно создан!')
                    return redirect('home')
            except Exception as e:
                messages.error(request, f'Ошибка при создании заказа: {str(e)}')
                return redirect('home')
        
        # Обрабатываем бронирование столика (обычная логика)
        guests_count = request.POST.get('guests_count')
        date = request.POST.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
        time = request.POST.get('time')  # время в формате HH:MM
        duration = request.POST.get('duration')  # 25, 55
        
        # Вычисляем дату начала (используем московское время)
        from datetime import datetime
        now = timezone.localtime(timezone.now())
        
        if date == 'today':
            start_date = now.date()
        elif date == 'tomorrow':
            start_date = (now + timedelta(days=1)).date()
        elif date == 'day_after_tomorrow':
            start_date = (now + timedelta(days=2)).date()
            # Если послезавтра - выходной, пропускаем до следующего рабочего дня
            while start_date.weekday() >= 5:
                start_date = start_date + timedelta(days=1)
        else:
            messages.error(request, 'Выберите дату бронирования.')
            return redirect('home')
        
        # Формируем datetime (в московской таймзоне)
        try:
            hour, minute = map(int, time.split(':'))
            naive_datetime = datetime.combine(start_date, datetime.min.time().replace(hour=hour, minute=minute))
            # Создаем aware datetime в московской таймзоне
            start_datetime = MOSCOW_TZ.localize(naive_datetime)

            duration_minutes = int(duration)
            end_datetime = start_datetime + timedelta(minutes=duration_minutes)
            
            guests_count_int = int(guests_count)
            
            # Автоматически выбираем подходящий столик
            table = find_available_table(guests_count_int, start_datetime, end_datetime)
            
            if not table:
                messages.error(request, 'К сожалению, на выбранное время нет свободных столиков подходящего размера. Пожалуйста, выберите другое время.')
                return redirect('home')

            reservation = Reservation(
                user=request.user,
                table=table,
                guests_count=guests_count_int,
                start_time=start_datetime,
                end_time=end_datetime
            )
            
            reservation.full_clean()
            reservation.save()
            
            # Обрабатываем предзаказ блюд
            from django.db.models import Sum
            dish_errors = []
            for key, value in request.POST.items():
                if key.startswith('dish_quantity_'):
                    dish_id = int(key.replace('dish_quantity_', ''))
                    quantity = int(value) if value else 0
                    
                    if quantity > 0:
                        try:
                            dish = Dish.objects.get(pk=dish_id)
                            
                            # Проверка доступности
                            reserved_result = ReservationDish.objects.filter(
                                dish=dish,
                                reservation__end_time__gte=timezone.now()
                            ).aggregate(total=Sum('quantity'))
                            reserved = reserved_result['total'] or 0
                            available = dish.available_quantity - reserved
                            
                            if quantity > available:
                                dish_errors.append(f'Блюдо "{dish.name}": недостаточно. Доступно: {available}')
                            else:
                                ReservationDish.objects.create(
                                    reservation=reservation,
                                    dish=dish,
                                    quantity=quantity
                                )
                        except (Dish.DoesNotExist, ValueError, ValidationError) as e:
                            dish_errors.append(f'Ошибка при добавлении блюда: {str(e)}')
            
            if dish_errors:
                messages.warning(request, 'Бронирование создано, но были ошибки при добавлении блюд: ' + '; '.join(dish_errors))
            else:
                messages.success(request, f'Бронирование успешно создано! Вам назначен столик №{table.table_number}.')
            return redirect('home')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Ошибка при обработке данных: {str(e)}')
        except ValidationError as e:
            for field, errors in e.error_dict.items():
                for error in errors:
                    messages.error(request, f'{error.message}')
    else:
        form = ReservationForm()
    
    reservations = Reservation.objects.filter(user=request.user).order_by('-start_time')[:10]
    
    # Генерируем доступные рабочие дни (используем московское время)
    now = timezone.localtime(timezone.now())
    available_dates = []
    from django.utils.formats import date_format
    
    # Сегодня
    if now.weekday() < 5:  # Понедельник-пятница
        available_dates.append(('today', 'Сегодня', now.date()))
    
    # Завтра
    tomorrow = (now + timedelta(days=1)).date()
    if tomorrow.weekday() < 5:
        available_dates.append(('tomorrow', 'Завтра', tomorrow))
    
    # Послезавтра (только рабочие дни, максимум 2 рабочих дня)
    day_after = (now + timedelta(days=2)).date()
    # Пропускаем выходные
    while day_after.weekday() >= 5:
        day_after = day_after + timedelta(days=1)
    
    # Проверяем, что не превышаем лимит в 2 рабочих дня
    working_days = 0
    check_date = now.date()
    while check_date < day_after:
        if check_date.weekday() < 5:
            working_days += 1
        check_date = check_date + timedelta(days=1)
    
    if working_days <= 2 and day_after.weekday() < 5:
        available_dates.append(('day_after_tomorrow', date_format(day_after, "d F"), day_after))
    
    # Генерируем доступные временные слоты
    time_slots = []
    for hour in range(12, 22):  # С 12:00 до 21:30
        for minute in [0, 30]:
            time_slots.append(f"{hour:02d}:{minute:02d}")
    
    # Получаем доступные блюда для предзаказа
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by('name')
    
    # Получаем блюда в меню для каждой доступной даты
    import json
    dishes_by_date = {}
    for date_key, date_label, date_obj in available_dates:
        dishes_by_date[date_key] = list(get_menu_dishes_for_date(date_obj))
    dishes_by_date_json = json.dumps(dishes_by_date)
    
    context = {
        'form': form,
        'reservations': reservations,
        'available_dates': available_dates,
        'time_slots': time_slots,
        'all_dishes': all_dishes,
        'dishes_by_date': dishes_by_date_json,
    }
    return render(request, 'home.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_detail(request, pk):
    reservation = get_object_or_404(Reservation, pk=pk, user=request.user)
    
    dishes = ReservationDish.objects.filter(reservation=reservation)
    can_modify = reservation.can_modify_or_cancel()
    
    context = {
        'reservation': reservation,
        'dishes': dishes,
        'can_modify': can_modify,
        'now': timezone.localtime(timezone.now()),
    }
    return render(request, 'bookings/reservation_detail.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_edit(request, pk):
    # Создаем UserProfile, если его нет
    UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'client'}
    )
    
    reservation = get_object_or_404(Reservation, pk=pk, user=request.user)
    
    if not reservation.can_modify_or_cancel():
        messages.error(request, 'Невозможно изменить бронирование. До начала осталось менее 30 минут.')
        return redirect('reservation_detail', pk=reservation.pk)
    
    if request.method == 'POST':
        # Обрабатываем данные из новой формы с кнопками
        guests_count = request.POST.get('guests_count')
        date = request.POST.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
        time = request.POST.get('time')  # время в формате HH:MM
        duration = request.POST.get('duration')  # 25, 55
        
        # Вычисляем дату начала (используем московское время)
        from datetime import datetime
        now = timezone.localtime(timezone.now())
        
        if date == 'today':
            start_date = now.date()
        elif date == 'tomorrow':
            start_date = (now + timedelta(days=1)).date()
        elif date == 'day_after_tomorrow':
            start_date = (now + timedelta(days=2)).date()
            # Если послезавтра - выходной, пропускаем до следующего рабочего дня
            while start_date.weekday() >= 5:
                start_date = start_date + timedelta(days=1)
        else:
            messages.error(request, 'Выберите дату бронирования.')
            return redirect('reservation_edit', pk=reservation.pk)
        
        # Формируем datetime (в московской таймзоне)
        try:
            hour, minute = map(int, time.split(':'))
            naive_datetime = datetime.combine(start_date, datetime.min.time().replace(hour=hour, minute=minute))
            # Создаем aware datetime в московской таймзоне
            start_datetime = MOSCOW_TZ.localize(naive_datetime)
            
            # Вычисляем end_time
            duration_minutes = int(duration)
            end_datetime = start_datetime + timedelta(minutes=duration_minutes)
            
            guests_count_int = int(guests_count)
            
            # Автоматически выбираем подходящий столик (исключаем текущее бронирование)
            table = find_available_table(guests_count_int, start_datetime, end_datetime, exclude_reservation_id=reservation.pk)
            
            if not table:
                messages.error(request, 'К сожалению, на выбранное время нет свободных столиков подходящего размера. Пожалуйста, выберите другое время.')
                return redirect('reservation_edit', pk=reservation.pk)
            
            # Обновляем бронирование
            reservation.table = table
            reservation.guests_count = guests_count_int
            reservation.start_time = start_datetime
            reservation.end_time = end_datetime
            
            reservation.full_clean()
            reservation.save()
            
            # Обрабатываем предзаказ блюд
            from django.db.models import Sum
            dish_errors = []
            
            # Удаляем все существующие блюда из предзаказа
            ReservationDish.objects.filter(reservation=reservation).delete()
            
            # Добавляем новые блюда
            for key, value in request.POST.items():
                if key.startswith('dish_quantity_'):
                    dish_id = int(key.replace('dish_quantity_', ''))
                    quantity = int(value) if value else 0
                    
                    if quantity > 0:
                        try:
                            dish = Dish.objects.get(pk=dish_id)
                            
                            # Проверка доступности
                            reserved_result = ReservationDish.objects.filter(
                                dish=dish,
                                reservation__end_time__gte=timezone.now()
                            ).aggregate(total=Sum('quantity'))
                            reserved = reserved_result['total'] or 0
                            available = dish.available_quantity - reserved
                            
                            if quantity > available:
                                dish_errors.append(f'Блюдо "{dish.name}": недостаточно. Доступно: {available}')
                            else:
                                ReservationDish.objects.create(
                                    reservation=reservation,
                                    dish=dish,
                                    quantity=quantity
                                )
                        except (Dish.DoesNotExist, ValueError, ValidationError) as e:
                            dish_errors.append(f'Ошибка при добавлении блюда: {str(e)}')
            
            if dish_errors:
                messages.warning(request, 'Бронирование изменено, но были ошибки при добавлении блюд: ' + '; '.join(dish_errors))
            else:
                messages.success(request, 'Бронирование успешно изменено!')
            return redirect('reservation_detail', pk=reservation.pk)
        except (ValueError, TypeError) as e:
            messages.error(request, f'Ошибка при обработке данных: {str(e)}')
        except ValidationError as e:
            for field, errors in e.error_dict.items():
                for error in errors:
                    messages.error(request, f'{error.message}')
    
    # Генерируем доступные рабочие дни (используем московское время)
    now = timezone.localtime(timezone.now())
    available_dates = []
    from django.utils.formats import date_format
    
    # Сегодня
    if now.weekday() < 5:  # Понедельник-пятница
        available_dates.append(('today', 'Сегодня', now.date()))
    
    # Завтра
    tomorrow = (now + timedelta(days=1)).date()
    if tomorrow.weekday() < 5:
        available_dates.append(('tomorrow', 'Завтра', tomorrow))
    
    # Послезавтра (только рабочие дни, максимум 2 рабочих дня)
    day_after = (now + timedelta(days=2)).date()
    while day_after.weekday() >= 5:
        day_after = day_after + timedelta(days=1)
    
    working_days = 0
    check_date = now.date()
    while check_date < day_after:
        if check_date.weekday() < 5:
            working_days += 1
        check_date = check_date + timedelta(days=1)
    
    if working_days <= 2 and day_after.weekday() < 5:
        available_dates.append(('day_after_tomorrow', date_format(day_after, "d F"), day_after))
    
    # Генерируем доступные временные слоты
    time_slots = []
    for hour in range(12, 22):  # С 12:00 до 21:30
        for minute in [0, 30]:
            time_slots.append(f"{hour:02d}:{minute:02d}")
    
    # Определяем текущие значения для предзаполнения
    reservation_date = reservation.start_time.date()
    reservation_time = reservation.start_time.strftime('%H:%M')
    reservation_duration_minutes = int((reservation.end_time - reservation.start_time).total_seconds() / 60)
    # Округляем к ближайшему значению из [25, 55]
    if reservation_duration_minutes <= 40:
        reservation_duration = 25
    else:
        reservation_duration = 55
    
    # Определяем, какая дата выбрана (сравниваем с доступными датами)
    selected_date_key = None
    now_date = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()
    
    if reservation_date == now_date:
        selected_date_key = 'today'
    elif reservation_date == tomorrow_date:
        selected_date_key = 'tomorrow'
    else:
        # Проверяем, попадает ли дата в "послезавтра" (с учетом рабочих дней)
        day_after_date = (now + timedelta(days=2)).date()
        while day_after_date.weekday() >= 5:
            day_after_date = day_after_date + timedelta(days=1)
        
        working_days = 0
        check_date = now_date
        while check_date < day_after_date:
            if check_date.weekday() < 5:
                working_days += 1
            check_date = check_date + timedelta(days=1)
        
        if working_days <= 2 and reservation_date == day_after_date:
            selected_date_key = 'day_after_tomorrow'
        else:
            # Если дата не соответствует ни одной из доступных, используем первую доступную
            selected_date_key = available_dates[0][0] if available_dates else 'today'
    
    # Получаем доступные блюда и текущие блюда в предзаказе
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by('name')
    current_dishes = ReservationDish.objects.filter(reservation=reservation)
    current_dishes_dict = {rd.dish_id: rd.quantity for rd in current_dishes}
    
    # Получаем блюда в меню для даты бронирования
    reservation_date = timezone.localtime(reservation.start_time).date()
    menu_dishes_for_date = list(get_menu_dishes_for_date(reservation_date))
    
    # Получаем блюда в меню для каждой доступной даты (для изменения даты)
    import json
    dishes_by_date = {}
    for date_key, date_label, date_obj in available_dates:
        dishes_by_date[date_key] = list(get_menu_dishes_for_date(date_obj))
    dishes_by_date_json = json.dumps(dishes_by_date)
    
    context = {
        'reservation': reservation,
        'available_dates': available_dates,
        'time_slots': time_slots,
        'selected_date_key': selected_date_key,
        'selected_time': reservation_time,
        'selected_duration': reservation_duration,
        'selected_guests_count': reservation.guests_count,
        'all_dishes': all_dishes,
        'current_dishes': current_dishes_dict,
        'menu_dishes_for_date': menu_dishes_for_date,
        'dishes_by_date': dishes_by_date_json,
    }
    return render(request, 'bookings/reservation_edit.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_delete(request, pk):
    reservation = get_object_or_404(Reservation, pk=pk, user=request.user)
    
    if not reservation.can_modify_or_cancel():
        messages.error(request, 'Невозможно отменить бронирование. До начала осталось менее 30 минут.')
        return redirect('reservation_detail', pk=reservation.pk)
    
    if request.method == 'POST':
        reservation.delete()
        messages.success(request, 'Бронирование успешно отменено!')
        return redirect('home')
    
    context = {
        'reservation': reservation,
    }
    return render(request, 'bookings/reservation_confirm_delete.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_dish_add(request, reservation_pk):
    """Добавление блюда в предзаказ"""
    reservation = get_object_or_404(Reservation, pk=reservation_pk, user=request.user)
    
    if not reservation.can_modify_or_cancel():
        messages.error(request, 'Невозможно изменить предзаказ. До начала осталось менее 30 минут.')
        return redirect('reservation_detail', pk=reservation.pk)
    
    if request.method == 'POST':
        dish_id = request.POST.get('dish')
        quantity = request.POST.get('quantity')
        
        try:
            dish = Dish.objects.get(pk=dish_id)
            quantity = int(quantity)
            
            if quantity <= 0:
                messages.error(request, 'Количество должно быть больше 0.')
                return redirect('reservation_detail', pk=reservation.pk)
            
            # Проверка доступности
            from django.db.models import Sum
            reserved_result = ReservationDish.objects.filter(
                dish=dish,
                reservation__end_time__gte=timezone.now()
            ).exclude(reservation=reservation).aggregate(
                total=Sum('quantity')
            )
            reserved = reserved_result['total'] or 0
            
            available = dish.available_quantity - reserved
            if quantity > available:
                messages.error(request, f'Недостаточно блюда. Доступно: {available}')
                return redirect('reservation_detail', pk=reservation.pk)
            
            reservation_dish, created = ReservationDish.objects.get_or_create(
                reservation=reservation,
                dish=dish,
                defaults={'quantity': quantity}
            )
            
            if not created:
                reservation_dish.quantity = quantity
                reservation_dish.save()
            
            messages.success(request, f'Блюдо "{dish.name}" добавлено в предзаказ!')
        except (Dish.DoesNotExist, ValueError) as e:
            messages.error(request, 'Ошибка при добавлении блюда.')
    
    return redirect('reservation_detail', pk=reservation.pk)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_dish_delete(request, reservation_pk, dish_pk):
    """Удаление блюда из предзаказа"""
    reservation = get_object_or_404(Reservation, pk=reservation_pk, user=request.user)
    reservation_dish = get_object_or_404(ReservationDish, pk=dish_pk, reservation=reservation)
    
    if not reservation.can_modify_or_cancel():
        messages.error(request, 'Невозможно изменить предзаказ. До начала осталось менее 30 минут.')
        return redirect('reservation_detail', pk=reservation.pk)
    
    if request.method == 'POST':
        reservation_dish.delete()
        messages.success(request, 'Блюдо удалено из предзаказа!')
        return redirect('reservation_detail', pk=reservation.pk)
    
    context = {
        'reservation': reservation,
        'reservation_dish': reservation_dish,
    }
    return render(request, 'bookings/reservation_dish_confirm_delete.html', context)


# ========== ЗАКАЗЫ НА ВЫНОС ==========

@login_required
@user_passes_test(is_client, login_url='/')
def takeout_order_create(request):
    """Создание заказа на вынос"""
    # Создаем UserProfile, если его нет
    UserProfile.objects.get_or_create(
        user=request.user,
        defaults={'role': 'client'}
    )
    
    if request.method == 'POST':
        try:
            # Создаем заказ
            order = TakeoutOrder.objects.create(user=request.user, status='pending')
            
            # Обрабатываем блюда из формы
            from django.db.models import Sum
            dish_errors = []
            
            for key, value in request.POST.items():
                if key.startswith('dish_quantity_'):
                    dish_id = int(key.replace('dish_quantity_', ''))
                    quantity = int(value) if value else 0
                    
                    if quantity > 0:
                        try:
                            dish = Dish.objects.get(pk=dish_id)
                            
                            # Проверка доступности
                            reserved_result = ReservationDish.objects.filter(
                                dish=dish,
                                reservation__end_time__gte=timezone.now()
                            ).aggregate(total=Sum('quantity'))
                            reserved_in_reservations = reserved_result['total'] or 0
                            
                            reserved_in_takeout = TakeoutOrderItem.objects.filter(
                                dish=dish,
                                order__status__in=['pending', 'preparing', 'ready']
                            ).aggregate(total=Sum('quantity'))['total'] or 0
                            
                            total_reserved = reserved_in_reservations + reserved_in_takeout
                            available = dish.available_quantity - total_reserved
                            
                            if quantity > available:
                                dish_errors.append(f'Блюдо "{dish.name}": недостаточно. Доступно: {available}')
                            else:
                                TakeoutOrderItem.objects.create(
                                    order=order,
                                    dish=dish,
                                    quantity=quantity
                                )
                        except (Dish.DoesNotExist, ValueError, ValidationError) as e:
                            dish_errors.append(f'Ошибка при добавлении блюда: {str(e)}')
            
            if dish_errors:
                order.delete()  # Удаляем заказ, если были ошибки
                messages.error(request, 'Ошибки при создании заказа: ' + '; '.join(dish_errors))
                return redirect('home')
            else:
                messages.success(request, f'Заказ на вынос #{order.pk} успешно создан!')
                return redirect('home')
        except Exception as e:
            messages.error(request, f'Ошибка при создании заказа: {str(e)}')
            return redirect('home')
    
    # Для GET запроса просто редиректим на главную
    return redirect('home')


# ========== ЛИЧНЫЙ КАБИНЕТ ОПЕРАТОРА ==========

@login_required
@user_passes_test(is_operator, login_url='/')
def operator_cabinet(request):
    """Личный кабинет оператора"""
    reservations = Reservation.objects.all().order_by('-start_time')[:20]
    tables = Table.objects.all().order_by('table_number')
    dishes = Dish.objects.all().order_by('name')
    
    context = {
        'reservations': reservations,
        'tables': tables,
        'dishes': dishes,
    }
    return render(request, 'bookings/operator_cabinet.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_reservations(request):
    """Просмотр всех бронирований оператором"""
    reservations = Reservation.objects.all().order_by('-start_time')
    
    # Фильтры
    table_id = request.GET.get('table')
    client_id = request.GET.get('client')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    
    if table_id:
        reservations = reservations.filter(table_id=table_id)
    if client_id:
        reservations = reservations.filter(user_id=client_id)
    if date_from:
        reservations = reservations.filter(start_time__gte=date_from)
    if date_to:
        reservations = reservations.filter(start_time__lte=date_to)
    
    # Пагинация
    paginator = Paginator(reservations, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    tables = Table.objects.all().order_by('table_number')
    from django.contrib.auth.models import User
    users = User.objects.filter(profile__role='client').order_by('first_name', 'last_name', 'username')
    
    context = {
        'reservations': page_obj,
        'tables': tables,
        'clients': users,  # Для обратной совместимости с шаблоном
        'page_obj': page_obj,
    }
    return render(request, 'bookings/operator_reservations.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_reservation_detail(request, pk):
    """Детальная информация о бронировании для оператора"""
    reservation = get_object_or_404(Reservation, pk=pk)
    dishes = ReservationDish.objects.filter(reservation=reservation)
    
    context = {
        'reservation': reservation,
        'dishes': dishes,
    }
    return render(request, 'bookings/operator_reservation_detail.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_reservation_delete(request, pk):
    """Удаление бронирования оператором"""
    reservation = get_object_or_404(Reservation, pk=pk)
    
    if request.method == 'POST':
        reservation.delete()
        messages.success(request, 'Бронирование успешно удалено!')
        return redirect('operator_reservations')
    
    context = {
        'reservation': reservation,
    }
    return render(request, 'bookings/operator_reservation_confirm_delete.html', context)


# ========== УПРАВЛЕНИЕ СТОЛИКАМИ (ОПЕРАТОР) ==========

@login_required
@user_passes_test(is_operator, login_url='/')
def operator_tables(request):
    """Список столиков для оператора"""
    tables = Table.objects.all().order_by('table_number')
    
    # Пагинация
    paginator = Paginator(tables, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'tables': page_obj,
        'page_obj': page_obj,
    }
    return render(request, 'bookings/operator_tables.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_table_create(request):
    """Создание столика"""
    if request.method == 'POST':
        table_number = request.POST.get('table_number')
        seats = request.POST.get('seats')
        
        try:
            table = Table.objects.create(
                table_number=table_number,
                seats=int(seats)
            )
            messages.success(request, f'Столик №{table_number} успешно создан!')
            return redirect('operator_table_detail', pk=table.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при создании столика: {str(e)}')
    
    return render(request, 'bookings/operator_table_form.html', {'action': 'create'})


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_table_detail(request, pk):
    """Детальная информация о столике"""
    table = get_object_or_404(Table, pk=pk)
    reservations = Reservation.objects.filter(table=table).order_by('-start_time')[:10]
    
    context = {
        'table': table,
        'reservations': reservations,
    }
    return render(request, 'bookings/operator_table_detail.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_table_edit(request, pk):
    """Редактирование столика"""
    table = get_object_or_404(Table, pk=pk)
    
    if request.method == 'POST':
        table.table_number = request.POST.get('table_number')
        table.seats = int(request.POST.get('seats'))
        try:
            table.full_clean()
            table.save()
            messages.success(request, 'Столик успешно изменен!')
            return redirect('operator_table_detail', pk=table.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при сохранении: {str(e)}')
    
    context = {
        'table': table,
        'action': 'edit',
    }
    return render(request, 'bookings/operator_table_form.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_table_delete(request, pk):
    """Удаление столика"""
    table = get_object_or_404(Table, pk=pk)
    
    if request.method == 'POST':
        try:
            table.delete()
            messages.success(request, 'Столик успешно удален!')
            return redirect('operator_tables')
        except Exception as e:
            messages.error(request, f'Невозможно удалить столик: {str(e)}')
            return redirect('operator_table_detail', pk=table.pk)
    
    context = {'table': table}
    return render(request, 'bookings/operator_table_confirm_delete.html', context)


# ========== УПРАВЛЕНИЕ БЛЮДАМИ (ОПЕРАТОР) ==========

@login_required
@user_passes_test(is_operator, login_url='/')
def operator_dishes(request):
    """Список блюд для оператора"""
    dishes = Dish.objects.all().order_by('name')
    
    # Пагинация
    paginator = Paginator(dishes, 10)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    context = {
        'dishes': page_obj,
        'page_obj': page_obj,
    }
    return render(request, 'bookings/operator_dishes.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_dish_create(request):
    """Создание блюда"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        price = float(request.POST.get('price', 0))
        available_quantity = int(request.POST.get('available_quantity', 0))
        image = request.FILES.get('image')
        
        try:
            dish = Dish.objects.create(
                name=name,
                description=description,
                price=price,
                available_quantity=available_quantity,
                image=image
            )
            messages.success(request, f'Блюдо "{name}" успешно создано!')
            return redirect('operator_dish_detail', pk=dish.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при создании блюда: {str(e)}')
    
    return render(request, 'bookings/operator_dish_form.html', {'action': 'create'})


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_dish_detail(request, pk):
    """Детальная информация о блюде"""
    dish = get_object_or_404(Dish, pk=pk)
    reservations = ReservationDish.objects.filter(dish=dish).order_by('-reservation__start_time')[:10]
    
    context = {
        'dish': dish,
        'reservations': reservations,
    }
    return render(request, 'bookings/operator_dish_detail.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_dish_edit(request, pk):
    """Редактирование блюда"""
    dish = get_object_or_404(Dish, pk=pk)
    
    if request.method == 'POST':
        dish.name = request.POST.get('name')
        dish.description = request.POST.get('description', '')
        dish.price = float(request.POST.get('price', 0))
        dish.available_quantity = int(request.POST.get('available_quantity', 0))
        
        if 'image' in request.FILES:
            dish.image = request.FILES['image']
        
        try:
            dish.full_clean()
            dish.save()
            messages.success(request, 'Блюдо успешно изменено!')
            return redirect('operator_dish_detail', pk=dish.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при сохранении: {str(e)}')
    
    context = {
        'dish': dish,
        'action': 'edit',
    }
    return render(request, 'bookings/operator_dish_form.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_dish_delete(request, pk):
    """Удаление блюда"""
    dish = get_object_or_404(Dish, pk=pk)
    
    if request.method == 'POST':
        try:
            dish.delete()
            messages.success(request, 'Блюдо успешно удалено!')
            return redirect('operator_dishes')
        except Exception as e:
            messages.error(request, f'Невозможно удалить блюдо: {str(e)}')
            return redirect('operator_dish_detail', pk=dish.pk)
    
    context = {'dish': dish}
    return render(request, 'bookings/operator_dish_confirm_delete.html', context)


@require_http_methods(["GET"])
def get_occupied_time_slots(request):
    """API endpoint для получения занятых временных слотов для столика и даты"""
    table_id = request.GET.get('table_id')
    date_str = request.GET.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
    reservation_id = request.GET.get('reservation_id')  # Для редактирования, чтобы исключить текущее бронирование
    
    if not table_id or not date_str:
        return JsonResponse({'error': 'Не указан table_id или date'}, status=400)
    
    try:
        table = Table.objects.get(pk=table_id)
    except Table.DoesNotExist:
        return JsonResponse({'error': 'Столик не найден'}, status=404)
    
    # Определяем дату начала (используем московское время)
    now = timezone.localtime(timezone.now())
    if date_str == 'today':
        target_date = now.date()
    elif date_str == 'tomorrow':
        target_date = (now + timedelta(days=1)).date()
    elif date_str == 'day_after_tomorrow':
        target_date = (now + timedelta(days=2)).date()
        while target_date.weekday() >= 5:
            target_date = target_date + timedelta(days=1)
    else:
        return JsonResponse({'error': 'Некорректная дата'}, status=400)
    
    # Находим все бронирования для этого столика на эту дату
    # target_date уже в московском времени, но нужно создать диапазон для фильтрации
    # Django ORM автоматически конвертирует aware datetime, но нужно создать в правильной таймзоне
    naive_start = datetime.combine(target_date, datetime.min.time())
    start_of_day = MOSCOW_TZ.localize(naive_start)
    end_of_day = start_of_day + timedelta(days=1)
    
    reservations = Reservation.objects.filter(
        table=table,
        start_time__gte=start_of_day,
        start_time__lt=end_of_day
    )
    
    # Исключаем текущее бронирование при редактировании
    if reservation_id:
        try:
            reservations = reservations.exclude(pk=int(reservation_id))
        except (ValueError, TypeError):
            pass
    
    # Формируем список занятых временных интервалов (конвертируем в московское время)
    occupied_slots = []
    for reservation in reservations:
        # Конвертируем UTC время из базы в московское время
        # timezone.localtime() автоматически использует TIME_ZONE из settings (Europe/Moscow)
        start_moscow = timezone.localtime(reservation.start_time)
        end_moscow = timezone.localtime(reservation.end_time)
        
        occupied_slots.append({
            'start': start_moscow.strftime('%H:%M'),  # Время в московской таймзоне
            'end': end_moscow.strftime('%H:%M'),  # Время в московской таймзоне
            'start_datetime': start_moscow.isoformat(),
            'end_datetime': end_moscow.isoformat(),
        })
    
    return JsonResponse({
        'occupied_slots': occupied_slots,
        'date': target_date.isoformat(),
    })


@require_http_methods(["GET"])
def check_available_time_slots(request):
    """API endpoint для проверки доступности временных слотов с учетом количества персон и длительности"""
    date_str = request.GET.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
    guests_count = request.GET.get('guests_count')
    
    if not date_str:
        return JsonResponse({'error': 'Не указана дата'}, status=400)
    
    if not guests_count:
        return JsonResponse({'error': 'Не указано количество персон'}, status=400)
    
    try:
        guests_count_int = int(guests_count)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Некорректное количество персон'}, status=400)
    
    # Определяем дату начала (используем московское время)
    now = timezone.localtime(timezone.now())
    if date_str == 'today':
        target_date = now.date()
    elif date_str == 'tomorrow':
        target_date = (now + timedelta(days=1)).date()
    elif date_str == 'day_after_tomorrow':
        target_date = (now + timedelta(days=2)).date()
        while target_date.weekday() >= 5:
            target_date = target_date + timedelta(days=1)
    else:
        return JsonResponse({'error': 'Некорректная дата'}, status=400)
    
    # Создаем диапазон для дня
    naive_start = datetime.combine(target_date, datetime.min.time())
    start_of_day = MOSCOW_TZ.localize(naive_start)
    end_of_day = start_of_day + timedelta(days=1)
    
    # Получаем все подходящие столики (с достаточным количеством мест)
    suitable_tables = Table.objects.filter(seats__gte=guests_count_int).order_by('seats')
    
    # Генерируем временные слоты
    time_slots = []
    for hour in range(12, 22):  # С 12:00 до 21:30
        for minute in [0, 30]:
            time_slots.append(f"{hour:02d}:{minute:02d}")
    
    # Варианты длительности
    durations = [25, 55]
    
    # Результат: какие временные слоты доступны для какой длительности
    available_slots = {}
    
    for duration in durations:
        available_slots[duration] = []
        for time_str in time_slots:
            hour, minute = map(int, time_str.split(':'))
            naive_datetime = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
            start_datetime = MOSCOW_TZ.localize(naive_datetime)
            end_datetime = start_datetime + timedelta(minutes=duration)
            
            # Проверяем, есть ли хотя бы один доступный столик на это время
            is_available = False
            for table in suitable_tables:
                # Проверяем пересечения с существующими бронированиями
                overlapping = Reservation.objects.filter(
                    table=table,
                    start_time__lt=end_datetime,
                    end_time__gt=start_datetime
                ).exists()
                
                if not overlapping:
                    is_available = True
                    break
            
            if is_available:
                available_slots[duration].append(time_str)
    
    return JsonResponse({
        'available_slots': available_slots,
        'date': target_date.isoformat(),
    })



# ========== УПРАВЛЕНИЕ МЕНЮ (ОПЕРАТОР) ==========

@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menus(request):
    """Список еженедельных меню для оператора (только рабочие дни)"""
    menus = WeeklyMenu.objects.filter(day_of_week__lt=5).order_by('day_of_week')  # Только рабочие дни (0-4)
    overrides = MenuOverride.objects.all().order_by('-date_from')[:10]
    
    context = {
        'menus': menus,
        'overrides': overrides,
    }
    return render(request, 'bookings/operator_menus.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_view_date(request):
    """Просмотр меню на выбранную дату"""
    date_str = request.GET.get('date')
    
    if not date_str:
        messages.error(request, 'Не указана дата')
        return redirect('operator_menus')
    
    try:
        from datetime import datetime
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_of_week = selected_date.weekday()  # 0=понедельник, 6=воскресенье
        
        # Получаем еженедельное меню для этого дня недели
        weekly_menu = None
        weekly_dishes = []
        try:
            weekly_menu = WeeklyMenu.objects.get(day_of_week=day_of_week, is_active=True)
            weekly_menu_items = WeeklyMenuItem.objects.filter(menu=weekly_menu).order_by('order', 'dish__name')
            weekly_dishes = [item.dish for item in weekly_menu_items]
        except WeeklyMenu.DoesNotExist:
            pass
        
        # Получаем активные переопределения для этой даты
        active_overrides = MenuOverride.objects.filter(
            is_active=True,
            date_from__lte=selected_date
        ).filter(
            Q(date_to__isnull=True) | Q(date_to__gte=selected_date)
        ).order_by('-date_from')
        
        # Применяем переопределения
        final_dishes = list(weekly_dishes)  # Начинаем с еженедельного меню
        dish_set = set(d.id for d in final_dishes)
        
        for override in active_overrides:
            override_items = MenuOverrideItem.objects.filter(override=override).order_by('order', 'dish__name')
            for item in override_items:
                if item.action == 'add':
                    if item.dish.id not in dish_set:
                        final_dishes.append(item.dish)
                        dish_set.add(item.dish.id)
                elif item.action == 'remove':
                    final_dishes = [d for d in final_dishes if d.id != item.dish.id]
                    dish_set.discard(item.dish.id)
        
        context = {
            'selected_date': selected_date,
            'day_of_week_name': ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'][day_of_week],
            'weekly_menu': weekly_menu,
            'weekly_dishes': weekly_dishes,
            'active_overrides': active_overrides,
            'final_dishes': final_dishes,
        }
        return render(request, 'bookings/operator_menu_view_date.html', context)
        
    except ValueError:
        messages.error(request, 'Неверный формат даты')
        return redirect('operator_menus')
    except Exception as e:
        messages.error(request, f'Ошибка при получении меню: {str(e)}')
        return redirect('operator_menus')


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menus_create_all(request):
    """Создание меню на рабочие дни недели (понедельник-пятница)"""
    all_dishes = Dish.objects.all().order_by('name')
    
    # Получаем существующие меню только для рабочих дней (0-4 = понедельник-пятница)
    working_days_data = []
    working_days = [0, 1, 2, 3, 4]  # Понедельник, Вторник, Среда, Четверг, Пятница
    day_names_list = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']
    
    for idx, day in enumerate(working_days):
        menu, created = WeeklyMenu.objects.get_or_create(day_of_week=day)
        working_days_data.append({
            'day': day,
            'day_name': day_names_list[idx],
            'menu': menu,
            'items': list(WeeklyMenuItem.objects.filter(menu=menu).values_list('dish_id', flat=True))
        })
    
    if request.method == 'POST':
        # Обрабатываем меню только для рабочих дней
        for day_data in working_days_data:
            day = day_data['day']
            menu = day_data['menu']
            menu.is_active = request.POST.get(f'day_{day}_active') == 'on'
            menu.save()
            
            # Удаляем все существующие блюда для этого дня
            WeeklyMenuItem.objects.filter(menu=menu).delete()
            
            # Добавляем выбранные блюда
            selected_dishes = request.POST.getlist(f'day_{day}_dishes')
            for idx, dish_id in enumerate(selected_dishes):
                try:
                    dish = Dish.objects.get(pk=int(dish_id))
                    WeeklyMenuItem.objects.create(
                        menu=menu,
                        dish=dish,
                        order=idx
                    )
                except (Dish.DoesNotExist, ValueError):
                    continue
        
        messages.success(request, 'Меню на рабочие дни успешно создано!')
        return redirect('operator_menus')
    
    context = {
        'all_dishes': all_dishes,
        'working_days_data': working_days_data,
    }
    return render(request, 'bookings/operator_menus_create_all.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_edit(request, day_of_week):
    """Редактирование меню на день недели"""
    menu, created = WeeklyMenu.objects.get_or_create(day_of_week=day_of_week)
    all_dishes = Dish.objects.all().order_by('name')
    menu_items = WeeklyMenuItem.objects.filter(menu=menu).order_by('order', 'dish__name')
    
    if request.method == 'POST':
        # Обработка формы
        menu.is_active = request.POST.get('is_active') == 'on'
        menu.save()
        
        # Удаляем все существующие блюда
        WeeklyMenuItem.objects.filter(menu=menu).delete()
        
        # Добавляем выбранные блюда
        selected_dishes = request.POST.getlist('dishes')
        for idx, dish_id in enumerate(selected_dishes):
            try:
                dish = Dish.objects.get(pk=int(dish_id))
                WeeklyMenuItem.objects.create(
                    menu=menu,
                    dish=dish,
                    order=idx
                )
            except (Dish.DoesNotExist, ValueError):
                continue
        
        messages.success(request, f'Меню на {menu.get_day_of_week_display()} успешно обновлено!')
        return redirect('operator_menus')
    
    selected_dish_ids = [item.dish.id for item in menu_items]
    
    context = {
        'menu': menu,
        'all_dishes': all_dishes,
        'selected_dish_ids': selected_dish_ids,
    }
    return render(request, 'bookings/operator_menu_edit.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_override_create(request):
    """Создание переопределения меню"""
    all_dishes = Dish.objects.all().order_by('name')
    
    if request.method == 'POST':
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to') or None
        is_active = request.POST.get('is_active') == 'on'
        
        try:
            from datetime import datetime
            override = MenuOverride.objects.create(
                date_from=datetime.strptime(date_from, '%Y-%m-%d').date(),
                date_to=datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else None,
                is_active=is_active
            )
            
            # Обрабатываем блюда для добавления
            for key, value in request.POST.items():
                if key.startswith('dish_') and key.endswith('_add') and value == 'on':
                    try:
                        dish_id = int(key.replace('dish_', '').replace('_add', ''))
                        dish = Dish.objects.get(pk=dish_id)
                        MenuOverrideItem.objects.create(
                            override=override,
                            dish=dish,
                            action='add',
                            order=0
                        )
                    except (Dish.DoesNotExist, ValueError):
                        continue
            
            # Обрабатываем блюда для удаления
            for key, value in request.POST.items():
                if key.startswith('dish_') and key.endswith('_remove') and value == 'on':
                    try:
                        dish_id = int(key.replace('dish_', '').replace('_remove', ''))
                        dish = Dish.objects.get(pk=dish_id)
                        MenuOverrideItem.objects.create(
                            override=override,
                            dish=dish,
                            action='remove',
                            order=0
                        )
                    except (Dish.DoesNotExist, ValueError):
                        continue
            
            messages.success(request, 'Переопределение меню успешно создано!')
            return redirect('operator_menu_override_detail', pk=override.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при создании переопределения: {str(e)}')
    
    context = {
        'all_dishes': all_dishes,
    }
    return render(request, 'bookings/operator_menu_override_form.html', {'action': 'create', 'all_dishes': all_dishes})


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_override_detail(request, pk):
    """Детальная информация о переопределении меню"""
    override = get_object_or_404(MenuOverride, pk=pk)
    items = MenuOverrideItem.objects.filter(override=override).order_by('order', 'dish__name')
    
    context = {
        'override': override,
        'items': items,
    }
    return render(request, 'bookings/operator_menu_override_detail.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_override_edit(request, pk):
    """Редактирование переопределения меню"""
    override = get_object_or_404(MenuOverride, pk=pk)
    all_dishes = Dish.objects.all().order_by('name')
    items = MenuOverrideItem.objects.filter(override=override).order_by('order', 'dish__name')
    
    if request.method == 'POST':
        date_from = request.POST.get('date_from')
        date_to = request.POST.get('date_to') or None
        is_active = request.POST.get('is_active') == 'on'
        
        try:
            from datetime import datetime
            override.date_from = datetime.strptime(date_from, '%Y-%m-%d').date()
            override.date_to = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else None
            override.is_active = is_active
            override.save()
            
            # Удаляем все существующие блюда
            MenuOverrideItem.objects.filter(override=override).delete()
            
            # Добавляем выбранные блюда
            for key, value in request.POST.items():
                if key.startswith('dish_') and value == 'on':
                    parts = key.split('_')
                    if len(parts) >= 3 and parts[1].isdigit():
                        dish_id = int(parts[1])
                        action = parts[2] if len(parts) > 2 else 'add'
                        
                        try:
                            dish = Dish.objects.get(pk=dish_id)
                            MenuOverrideItem.objects.create(
                                override=override,
                                dish=dish,
                                action=action,
                                order=0
                            )
                        except (Dish.DoesNotExist, ValueError):
                            continue
            
            messages.success(request, 'Переопределение меню успешно обновлено!')
            return redirect('operator_menu_override_detail', pk=override.pk)
        except Exception as e:
            messages.error(request, f'Ошибка при сохранении: {str(e)}')
    
    selected_dishes_add = [item.dish.id for item in items if item.action == 'add']
    selected_dishes_remove = [item.dish.id for item in items if item.action == 'remove']
    
    context = {
        'override': override,
        'all_dishes': all_dishes,
        'selected_dishes_add': selected_dishes_add,
        'selected_dishes_remove': selected_dishes_remove,
        'action': 'edit',
    }
    return render(request, 'bookings/operator_menu_override_form.html', context)


@login_required
@user_passes_test(is_operator, login_url='/')
def operator_menu_override_delete(request, pk):
    """Удаление переопределения меню"""
    override = get_object_or_404(MenuOverride, pk=pk)
    
    if request.method == 'POST':
        override.delete()
        messages.success(request, 'Переопределение меню успешно удалено!')
        return redirect('operator_menus')
    
    context = {'override': override}
    return render(request, 'bookings/operator_menu_override_confirm_delete.html', context)
