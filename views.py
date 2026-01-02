from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from datetime import timedelta, datetime
import pytz
from .models import Client, Table, Dish, Reservation, ReservationDish, UserProfile
from .forms import ReservationForm

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

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
        # Если есть Client, связываем с пользователем
        try:
            client = Client.objects.filter(email=request.user.email).first()
            if client and not client.user:
                client.user = request.user
                client.save()
        except:
            pass
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
    
    # Создаем клиента, если его нет
    client, created = Client.objects.get_or_create(
        user=request.user,
        defaults={
            'full_name': f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username,
            'email': request.user.email or f'{request.user.username}@example.com'
        }
    )
    
    if request.method == 'POST':
        # Обрабатываем данные из новой формы с кнопками
        table_id = request.POST.get('table')
        guests_count = request.POST.get('guests_count')
        date = request.POST.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
        time = request.POST.get('time')  # время в формате HH:MM
        duration = request.POST.get('duration')  # 15, 30, 45
        
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

            reservation = Reservation(
                client=client,
                table_id=table_id,
                guests_count=int(guests_count),
                start_time=start_datetime,
                end_time=end_datetime
            )
            
            reservation.full_clean()
            reservation.save()
            messages.success(request, 'Бронирование успешно создано!')
            return redirect('home')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Ошибка при обработке данных: {str(e)}')
        except ValidationError as e:
            for field, errors in e.error_dict.items():
                for error in errors:
                    messages.error(request, f'{error.message}')
    else:
        form = ReservationForm()
    
    tables = Table.objects.all().order_by('table_number')
    reservations = Reservation.objects.filter(client=client).order_by('-start_time')[:10]
    
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
    
    context = {
        'form': form,
        'tables': tables,
        'reservations': reservations,
        'client': client,
        'available_dates': available_dates,
        'time_slots': time_slots,
    }
    return render(request, 'home.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_detail(request, pk):
    try:
        client = Client.objects.get(user=request.user)
        reservation = get_object_or_404(Reservation, pk=pk, client=client)
    except Client.DoesNotExist:
        messages.error(request, 'Профиль клиента не найден.')
        return redirect('home')
    
    dishes = ReservationDish.objects.filter(reservation=reservation)
    can_modify = reservation.can_modify_or_cancel()
    all_dishes = Dish.objects.filter(available_quantity__gt=0).order_by('name')
    
    context = {
        'reservation': reservation,
        'dishes': dishes,
        'can_modify': can_modify,
        'all_dishes': all_dishes,
        'now': timezone.localtime(timezone.now()),
    }
    return render(request, 'bookings/reservation_detail.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_edit(request, pk):
    # Создаем клиента, если его нет
    client, created = Client.objects.get_or_create(
        user=request.user,
        defaults={
            'full_name': f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username,
            'email': request.user.email or f'{request.user.username}@example.com'
        }
    )
    
    reservation = get_object_or_404(Reservation, pk=pk, client=client)
    
    if not reservation.can_modify_or_cancel():
        messages.error(request, 'Невозможно изменить бронирование. До начала осталось менее 30 минут.')
        return redirect('reservation_detail', pk=reservation.pk)
    
    if request.method == 'POST':
        # Обрабатываем данные из новой формы с кнопками
        table_id = request.POST.get('table')
        guests_count = request.POST.get('guests_count')
        date = request.POST.get('date')  # 'today', 'tomorrow', 'day_after_tomorrow'
        time = request.POST.get('time')  # время в формате HH:MM
        duration = request.POST.get('duration')  # 15, 30, 45
        
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
            
            # Обновляем бронирование
            reservation.table_id = table_id
            reservation.guests_count = int(guests_count)
            reservation.start_time = start_datetime
            reservation.end_time = end_datetime
            
            reservation.full_clean()
            reservation.save()
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
    reservation_duration = int((reservation.end_time - reservation.start_time).total_seconds() / 60)
    
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
    
    tables = Table.objects.all().order_by('table_number')
    
    context = {
        'reservation': reservation,
        'tables': tables,
        'available_dates': available_dates,
        'time_slots': time_slots,
        'selected_date_key': selected_date_key,
        'selected_time': reservation_time,
        'selected_duration': reservation_duration,
        'selected_table_id': reservation.table_id,
        'selected_guests_count': reservation.guests_count,
    }
    return render(request, 'bookings/reservation_edit.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_delete(request, pk):
    try:
        client = Client.objects.get(user=request.user)
        reservation = get_object_or_404(Reservation, pk=pk, client=client)
    except Client.DoesNotExist:
        messages.error(request, 'Профиль клиента не найден.')
        return redirect('home')
    
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
    try:
        client = Client.objects.get(user=request.user)
        reservation = get_object_or_404(Reservation, pk=reservation_pk, client=client)
    except Client.DoesNotExist:
        messages.error(request, 'Профиль клиента не найден.')
        return redirect('home')
    
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
    try:
        client = Client.objects.get(user=request.user)
        reservation = get_object_or_404(Reservation, pk=reservation_pk, client=client)
        reservation_dish = get_object_or_404(ReservationDish, pk=dish_pk, reservation=reservation)
    except Client.DoesNotExist:
        messages.error(request, 'Профиль клиента не найден.')
        return redirect('home')
    
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
        reservations = reservations.filter(client_id=client_id)
    if date_from:
        reservations = reservations.filter(start_time__gte=date_from)
    if date_to:
        reservations = reservations.filter(start_time__lte=date_to)
    
    tables = Table.objects.all().order_by('table_number')
    clients = Client.objects.all().order_by('full_name')
    
    context = {
        'reservations': reservations,
        'tables': tables,
        'clients': clients,
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


# ========== УПРАВЛЕНИЕ СТОЛИКАМИ (ОПЕРАТОР) ==========

@login_required
@user_passes_test(is_operator, login_url='/')
def operator_tables(request):
    """Список столиков для оператора"""
    tables = Table.objects.all().order_by('table_number')
    context = {'tables': tables}
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
    context = {'dishes': dishes}
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

