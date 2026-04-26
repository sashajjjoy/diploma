from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Avg, Count, Q
from django.core.paginator import Paginator
from datetime import timedelta, datetime
import pytz
from decimal import Decimal

from .models import (
    Booking,
    CustomerOrder,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    Table,
    Dish,
    UserProfile,
    WeeklyMenuDaySettings,
    WeeklyMenuItem,
    MenuOverride,
    MenuOverrideItem,
    News,
    VenueComplaint,
    Promotion,
    PromotionComboItem,
)
from .forms import ReservationForm
from .services.availability import occupied_slots_for_table_date, available_slots_for_date
from .services.reservations import (
    booking_detail_queryset,
    cancel_reservation_for_client,
    create_dish_review,
    create_or_update_reservation_for_client,
    get_booking_or_404_for_user,
    get_order_or_404_for_user,
    get_public_id,
    is_order_completed_for_review,
    order_detail_queryset,
)
from .services.promotions import (
    get_orderable_promotions,
    parse_dish_quantities_from_post,
    order_subtotal,
    compute_order_totals,
    promotion_price_preview,
    resolve_promotions_for_checkout,
    unit_price_after_single_promo,
)

Reservation = Booking
ReservationDish = OrderItem
DishReview = OrderItemReview
ReservationAppliedPromotion = OrderAppliedPromotion

MOSCOW_TZ = pytz.timezone('Europe/Moscow')


def client_home_promotion_context():
    promos = list(get_orderable_promotions())
    combo_promotions = [p for p in promos if p.kind == Promotion.KIND_COMBO]
    single_promos_by_dish = {}
    for p in promos:
        if p.kind != Promotion.KIND_SINGLE or not p.target_dish_id:
            continue
        d = p.target_dish
        if not d:
            continue
        single_promos_by_dish.setdefault(d.pk, []).append(
            {
                'promo': p,
                'original_price': Decimal(d.price).quantize(Decimal('0.01')),
                'price_new': unit_price_after_single_promo(d, p),
            }
        )
    combo_promotions_data = []
    for promotion in combo_promotions:
        preview = promotion_price_preview(promotion)
        combo_promotions_data.append(
            {
                "promo": promotion,
                "original_price": preview["original_price"],
                "price_new": preview["new_price"],
            }
        )
    return {
        'active_promotions': promos,
        'combo_promotions': combo_promotions_data,
        'single_promos_by_dish': single_promos_by_dish,
    }


def _ordered_dishes_for_ids(dish_ids):
    dishes_by_id = Dish.objects.in_bulk(dish_ids)
    return [dishes_by_id[dish_id] for dish_id in dish_ids if dish_id in dishes_by_id]


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
        day_settings = WeeklyMenuDaySettings.objects.get(day_of_week=day_of_week, is_active=True)
        weekly_menu_items = WeeklyMenuItem.objects.filter(day_settings=day_settings).order_by(
            'order', 'dish__name'
        )
        weekly_dishes = [item.dish for item in weekly_menu_items]
    except WeeklyMenuDaySettings.DoesNotExist:
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
        overlapping_query = Booking.objects.exclude(status=Booking.STATUS_CANCELLED).filter(
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


def is_operator_app(user):
    return is_operator(user)


def is_operator_or_admin(user):
    return is_operator(user)


def is_admin_app(user):
    """Доступ к кабинету администратора в приложении."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.is_admin()
    except UserProfile.DoesNotExist:
        return False


@login_required
def dashboard(request):
    if request.user.is_superuser:
        return redirect('admin_cabinet')
    try:
        profile = request.user.profile
        if profile.is_client():
            return redirect('home')
        elif profile.is_operator():
            return redirect('operator_cabinet')
        elif profile.is_admin():
            return redirect('admin_cabinet')
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

                menu_ids = get_menu_dishes_for_date(start_date)
                regular_map = parse_dish_quantities_from_post(request.POST)
                promos, per_promo, disc_amt, promo_err, dish_qty_map = resolve_promotions_for_checkout(
                    request.POST, regular_map, menu_ids
                )
                if promo_err:
                    messages.error(request, promo_err)
                    return redirect('home')
                has_dishes = sum(dish_qty_map.values()) > 0
                if not has_dishes:
                    messages.error(
                        request,
                        'Для заказа на вынос выберите блюда в меню и/или отметьте акцию (комбо или скидка на блюдо).',
                    )
                    return redirect('home')

                dishes_by_id_pre = {
                    d.pk: d for d in Dish.objects.filter(pk__in=list(dish_qty_map.keys()))
                }
                subtotal_amt, order_total_amt = compute_order_totals(
                    dish_qty_map, dishes_by_id_pre, disc_amt
                )

                reservation = Reservation(
                    user=request.user,
                    table=None,
                    guests_count=1,
                    start_time=start_datetime,
                    end_time=end_datetime,
                    applied_promotion=promos[0] if promos else None,
                    promotion_discount_total=disc_amt,
                    order_subtotal=subtotal_amt,
                    order_total=order_total_amt,
                )
                reservation.full_clean()
                reservation.save()
                for p, amt in per_promo:
                    ReservationAppliedPromotion.objects.create(
                        reservation=reservation, promotion=p, discount_amount=amt
                    )

                from django.db.models import Sum
                dish_errors = []
                for dish_id, quantity in dish_qty_map.items():
                    try:
                        dish = Dish.objects.get(pk=dish_id)
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
                    reservation.delete()
                    messages.error(request, 'Ошибки при создании заказа: ' + '; '.join(dish_errors))
                    return redirect('home')

                extra = f' Итого к оплате: {order_total_amt} ₽.'
                if promos and disc_amt and disc_amt > 0:
                    extra = (
                        f' Итого к оплате: {order_total_amt} ₽ '
                        f'(позиции: {subtotal_amt} ₽, скидка по акциям: {disc_amt} ₽).'
                    )
                messages.success(
                    request,
                    f'Заказ на вынос на {start_date.strftime("%d.%m.%Y")} успешно создан!{extra}',
                )
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

            menu_ids = get_menu_dishes_for_date(start_date)
            regular_map = parse_dish_quantities_from_post(request.POST)
            promos, per_promo, disc_amt, promo_err, dish_qty_map = resolve_promotions_for_checkout(
                request.POST, regular_map, menu_ids
            )
            if promo_err:
                messages.error(request, promo_err)
                return redirect('home')

            dishes_by_id_pre = {
                d.pk: d for d in Dish.objects.filter(pk__in=list(dish_qty_map.keys()))
            }
            subtotal_amt, order_total_amt = compute_order_totals(
                dish_qty_map, dishes_by_id_pre, disc_amt
            )

            reservation = Reservation(
                user=request.user,
                table=table,
                guests_count=guests_count_int,
                start_time=start_datetime,
                end_time=end_datetime,
                applied_promotion=promos[0] if promos else None,
                promotion_discount_total=disc_amt,
                order_subtotal=subtotal_amt,
                order_total=order_total_amt,
            )

            reservation.full_clean()
            reservation.save()
            for p, amt in per_promo:
                ReservationAppliedPromotion.objects.create(
                    reservation=reservation, promotion=p, discount_amount=amt
                )

            from django.db.models import Sum
            dish_errors = []
            for dish_id, quantity in dish_qty_map.items():
                if quantity <= 0:
                    continue
                try:
                    dish = Dish.objects.get(pk=dish_id)
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
                msg = f'Бронирование успешно создано! Вам назначен столик №{table.table_number}.'
                if dish_qty_map:
                    msg += f' Итого к оплате: {order_total_amt} ₽.'
                    if promos and disc_amt and disc_amt > 0:
                        msg += f' (позиции {subtotal_amt} ₽, скидка {disc_amt} ₽).'
                messages.success(request, msg)
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
    today_menu_ids = list(get_menu_dishes_for_date(now.date()))
    today_menu_dishes = _ordered_dishes_for_ids(today_menu_ids)
    
    context = {
        'form': form,
        'reservations': reservations,
        'available_dates': available_dates,
        'time_slots': time_slots,
        'all_dishes': all_dishes,
        'today_menu_dishes': today_menu_dishes,
        'today_menu_date': now.date(),
        'dishes_by_date': dishes_by_date_json,
        **client_home_promotion_context(),
    }
    return render(request, 'home.html', context)


@login_required
@user_passes_test(is_client, login_url='/')
def reservation_detail(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.select_related('applied_promotion', 'table').prefetch_related(
            'applied_promotion_links__promotion'
        ),
        pk=pk,
        user=request.user,
    )
    
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
            lines = ReservationDish.objects.filter(reservation=reservation).select_related('dish')
            qty_map = {rd.dish_id: rd.quantity for rd in lines}
            dishes_by = {rd.dish_id: rd.dish for rd in lines}
            sub = order_subtotal(qty_map, dishes_by)
            reservation.applied_promotion_links.all().delete()
            reservation.applied_promotion = None
            reservation.promotion_discount_total = Decimal('0')
            reservation.order_subtotal = sub
            reservation.order_total = sub
            reservation.save(update_fields=[
                'applied_promotion', 'promotion_discount_total',
                'order_subtotal', 'order_total',
            ])
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


# ========== КАБИНЕТ АДМИНИСТРАТОРА ==========

LOW_STOCK_THRESHOLD = 5


@login_required
@user_passes_test(is_admin_app, login_url='/')
def admin_cabinet(request):
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_date = local_now.date()
    week_ago = now - timedelta(days=7)

    reservations_today = Reservation.objects.filter(start_time__date=today_date).count()
    reservations_active = Reservation.objects.filter(
        start_time__lte=now, end_time__gte=now
    ).count()
    reservations_completed_week = Reservation.objects.filter(
        end_time__lt=now, end_time__gte=week_ago
    ).count()
    complaints_new = VenueComplaint.objects.filter(status='new').count()
    reviews_total = DishReview.objects.count()
    dishes_low_stock = list(
        Dish.objects.filter(available_quantity__lt=LOW_STOCK_THRESHOLD)
        .order_by('available_quantity', 'name')[:12]
    )
    users_by_role = list(
        UserProfile.objects.values('role').annotate(n=Count('id')).order_by('role')
    )
    recent_reservations = (
        Reservation.objects.select_related('user', 'table')
        .order_by('-created_at')[:10]
    )
    recent_complaints = (
        VenueComplaint.objects.select_related('user').order_by('-created_at')[:8]
    )
    recent_reviews = (
        DishReview.objects.select_related('user', 'dish').order_by('-created_at')[:8]
    )

    return render(
        request,
        'bookings/admin_cabinet.html',
        {
            'reservations_today': reservations_today,
            'reservations_active': reservations_active,
            'reservations_completed_week': reservations_completed_week,
            'complaints_new': complaints_new,
            'reviews_total': reviews_total,
            'dishes_low_stock': dishes_low_stock,
            'users_by_role': users_by_role,
            'recent_reservations': recent_reservations,
            'recent_complaints': recent_complaints,
            'recent_reviews': recent_reviews,
            'low_stock_threshold': LOW_STOCK_THRESHOLD,
        },
    )


# ========== ЛИЧНЫЙ КАБИНЕТ ОПЕРАТОРА ==========

@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_dish_detail(request, pk):
    """Детальная информация о блюде"""
    dish = get_object_or_404(Dish, pk=pk)
    reservations = (
        ReservationDish.objects.filter(dish=dish, order__booking__isnull=False)
        .select_related('order__booking', 'order__user')
        .order_by('-order__booking__start_time')[:10]
    )
    
    context = {
        'dish': dish,
        'reservations': reservations,
    }
    return render(request, 'bookings/operator_dish_detail.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menus(request):
    """Список еженедельных меню для оператора (только рабочие дни)"""
    menus = WeeklyMenuDaySettings.objects.filter(day_of_week__lt=5).order_by('day_of_week')
    overrides = MenuOverride.objects.all().order_by('-date_from')[:10]
    
    context = {
        'menus': menus,
        'overrides': overrides,
    }
    return render(request, 'bookings/operator_menus.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
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
            weekly_menu = WeeklyMenuDaySettings.objects.get(day_of_week=day_of_week, is_active=True)
            weekly_menu_items = WeeklyMenuItem.objects.filter(day_settings=weekly_menu).order_by(
                'order', 'dish__name'
            )
            weekly_dishes = [item.dish for item in weekly_menu_items]
        except WeeklyMenuDaySettings.DoesNotExist:
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
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menus_create_all(request):
    """Создание меню на рабочие дни недели (понедельник-пятница)"""
    all_dishes = Dish.objects.all().order_by('name')
    
    # Получаем существующие меню только для рабочих дней (0-4 = понедельник-пятница)
    working_days_data = []
    working_days = [0, 1, 2, 3, 4]  # Понедельник, Вторник, Среда, Четверг, Пятница
    day_names_list = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']
    
    for idx, day in enumerate(working_days):
        menu, created = WeeklyMenuDaySettings.objects.get_or_create(day_of_week=day)
        working_days_data.append({
            'day': day,
            'day_name': day_names_list[idx],
            'menu': menu,
            'items': list(WeeklyMenuItem.objects.filter(day_settings=menu).values_list('dish_id', flat=True))
        })
    
    if request.method == 'POST':
        # Обрабатываем меню только для рабочих дней
        for day_data in working_days_data:
            day = day_data['day']
            menu = day_data['menu']
            menu.is_active = request.POST.get(f'day_{day}_active') == 'on'
            menu.save()
            
            # Удаляем все существующие блюда для этого дня
            WeeklyMenuItem.objects.filter(day_settings=menu).delete()
            
            # Добавляем выбранные блюда
            selected_dishes = request.POST.getlist(f'day_{day}_dishes')
            for idx, dish_id in enumerate(selected_dishes):
                try:
                    dish = Dish.objects.get(pk=int(dish_id))
                    WeeklyMenuItem.objects.create(
                        day_settings=menu,
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
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menu_edit(request, day_of_week):
    """Редактирование меню на день недели"""
    menu, created = WeeklyMenuDaySettings.objects.get_or_create(day_of_week=day_of_week)
    all_dishes = Dish.objects.all().order_by('name')
    menu_items = WeeklyMenuItem.objects.filter(day_settings=menu).order_by('order', 'dish__name')
    
    if request.method == 'POST':
        # Обработка формы
        menu.is_active = request.POST.get('is_active') == 'on'
        menu.save()
        
        # Удаляем все существующие блюда
        WeeklyMenuItem.objects.filter(day_settings=menu).delete()
        
        # Добавляем выбранные блюда
        selected_dishes = request.POST.getlist('dishes')
        for idx, dish_id in enumerate(selected_dishes):
            try:
                dish = Dish.objects.get(pk=int(dish_id))
                WeeklyMenuItem.objects.create(
                    day_settings=menu,
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
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
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menu_override_delete(request, pk):
    """Удаление переопределения меню"""
    override = get_object_or_404(MenuOverride, pk=pk)
    
    if request.method == 'POST':
        override.delete()
        messages.success(request, 'Переопределение меню успешно удалено!')
        return redirect('operator_menus')
    
    context = {'override': override}
    return render(request, 'bookings/operator_menu_override_confirm_delete.html', context)


def _parse_promo_datetime(post, key):
    s = (post.get(key) or '').strip()
    if not s:
        return None
    try:
        if 'T' in s:
            dt_naive = datetime.strptime(s[:16], '%Y-%m-%dT%H:%M')
        else:
            dt_naive = datetime.strptime(s, '%Y-%m-%d %H:%M')
        return MOSCOW_TZ.localize(dt_naive)
    except ValueError:
        return None


# ---------- Оператор: новости ----------
@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_news_list(request):
    items = News.objects.all().order_by('-published_at')
    return render(request, 'bookings/operator_news_list.html', {'news_list': items})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_news_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        summary = request.POST.get('summary', '').strip()
        body = request.POST.get('body', '').strip()
        is_published = request.POST.get('is_published') == 'on'
        published_at = _parse_promo_datetime(request.POST, 'published_at_local') or timezone.now()
        if not title or not body:
            messages.error(request, 'Заполните заголовок и текст.')
        else:
            News.objects.create(
                title=title,
                summary=summary,
                body=body,
                is_published=is_published,
                published_at=published_at,
            )
            messages.success(request, 'Новость создана.')
            return redirect('operator_news_list')
    return render(request, 'bookings/operator_news_form.html', {'action': 'create', 'news_item': None})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_news_edit(request, pk):
    news_item = get_object_or_404(News, pk=pk)
    if request.method == 'POST':
        news_item.title = request.POST.get('title', '').strip()
        news_item.summary = request.POST.get('summary', '').strip()
        news_item.body = request.POST.get('body', '').strip()
        news_item.is_published = request.POST.get('is_published') == 'on'
        dt = _parse_promo_datetime(request.POST, 'published_at_local')
        if dt:
            news_item.published_at = dt
        news_item.save()
        messages.success(request, 'Новость сохранена.')
        return redirect('operator_news_list')
    return render(request, 'bookings/operator_news_form.html', {'action': 'edit', 'news_item': news_item})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_news_delete(request, pk):
    news_item = get_object_or_404(News, pk=pk)
    if request.method == 'POST':
        news_item.delete()
        messages.success(request, 'Новость удалена.')
        return redirect('operator_news_list')
    return render(request, 'bookings/operator_news_confirm_delete.html', {'news_item': news_item})


# ---------- Оператор: акции ----------
@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_promotion_list(request):
    promos = Promotion.objects.all().order_by('-valid_from')
    return render(request, 'bookings/operator_promotion_list.html', {'promotions': promos})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_promotion_create(request):
    all_dishes = Dish.objects.all().order_by('name')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        kind = request.POST.get('kind', Promotion.KIND_SINGLE)
        discount_type = request.POST.get('discount_type', Promotion.DISCOUNT_PERCENT)
        try:
            discount_value = Decimal(request.POST.get('discount_value', '0'))
        except Exception:
            discount_value = Decimal('0')
        is_active = request.POST.get('is_active') == 'on'
        vf = _parse_promo_datetime(request.POST, 'valid_from_local')
        vt = _parse_promo_datetime(request.POST, 'valid_to_local')
        if not name or not vf or not vt:
            messages.error(request, 'Укажите название и период действия.')
            return render(
                request,
                'bookings/operator_promotion_form.html',
                {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
            )
        if vf >= vt:
            messages.error(request, 'Дата «до» должна быть позже «с».')
            return render(
                request,
                'bookings/operator_promotion_form.html',
                {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
            )
        target_id = request.POST.get('target_dish') or ''
        target_dish = None
        if kind == Promotion.KIND_SINGLE:
            if not target_id:
                messages.error(request, 'Выберите блюдо для акции.')
                return render(
                    request,
                    'bookings/operator_promotion_form.html',
                    {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
                )
            target_dish = get_object_or_404(Dish, pk=int(target_id))
        p = Promotion(
            name=name,
            description=description,
            kind=kind,
            discount_type=discount_type,
            discount_value=discount_value,
            valid_from=vf,
            valid_to=vt,
            is_active=is_active,
            target_dish=target_dish,
        )
        try:
            p.full_clean()
            p.save()
        except ValidationError as e:
            messages.error(request, str(e))
            return render(
                request,
                'bookings/operator_promotion_form.html',
                {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
            )
        if kind == Promotion.KIND_COMBO:
            dish_ids = request.POST.getlist('combo_dish_id')
            min_qs = request.POST.getlist('combo_min_qty')
            for i, did in enumerate(dish_ids):
                if not did:
                    continue
                try:
                    mq = int(min_qs[i]) if i < len(min_qs) else 1
                    mq = max(1, mq)
                    d = Dish.objects.get(pk=int(did))
                    PromotionComboItem.objects.create(promotion=p, dish=d, min_quantity=mq)
                except (ValueError, Dish.DoesNotExist, IndexError):
                    continue
            if not p.combo_items.exists():
                p.delete()
                messages.error(request, 'Добавьте хотя бы одно блюдо в комбо.')
                return render(
                    request,
                    'bookings/operator_promotion_form.html',
                    {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
                )
        messages.success(request, 'Акция создана.')
        return redirect('operator_promotion_list')
    return render(
        request,
        'bookings/operator_promotion_form.html',
        {'action': 'create', 'promotion': None, 'all_dishes': all_dishes, 'combo_rows': []},
    )


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_promotion_edit(request, pk):
    promotion = get_object_or_404(Promotion.objects.prefetch_related('combo_items'), pk=pk)
    all_dishes = Dish.objects.all().order_by('name')
    combo_rows = list(promotion.combo_items.select_related('dish').all())
    if request.method == 'POST':
        promotion.name = request.POST.get('name', '').strip()
        promotion.description = request.POST.get('description', '').strip()
        promotion.kind = request.POST.get('kind', Promotion.KIND_SINGLE)
        promotion.discount_type = request.POST.get('discount_type', Promotion.DISCOUNT_PERCENT)
        try:
            promotion.discount_value = Decimal(request.POST.get('discount_value', '0'))
        except Exception:
            pass
        promotion.is_active = request.POST.get('is_active') == 'on'
        vf = _parse_promo_datetime(request.POST, 'valid_from_local')
        vt = _parse_promo_datetime(request.POST, 'valid_to_local')
        if vf and vt:
            promotion.valid_from = vf
            promotion.valid_to = vt
        tid = request.POST.get('target_dish') or ''
        if promotion.kind == Promotion.KIND_SINGLE and tid:
            promotion.target_dish = get_object_or_404(Dish, pk=int(tid))
        else:
            promotion.target_dish = None
        try:
            promotion.full_clean()
            promotion.save()
        except ValidationError as e:
            messages.error(request, str(e))
            return render(
                request,
                'bookings/operator_promotion_form.html',
                {
                    'action': 'edit',
                    'promotion': promotion,
                    'all_dishes': all_dishes,
                    'combo_rows': combo_rows,
                },
            )
        promotion.combo_items.all().delete()
        if promotion.kind == Promotion.KIND_COMBO:
            dish_ids = request.POST.getlist('combo_dish_id')
            min_qs = request.POST.getlist('combo_min_qty')
            for i, did in enumerate(dish_ids):
                if not did:
                    continue
                try:
                    mq = int(min_qs[i]) if i < len(min_qs) else 1
                    mq = max(1, mq)
                    d = Dish.objects.get(pk=int(did))
                    PromotionComboItem.objects.create(promotion=promotion, dish=d, min_quantity=mq)
                except (ValueError, Dish.DoesNotExist, IndexError):
                    continue
            if not promotion.combo_items.exists():
                messages.error(request, 'Комбо должно содержать хотя бы одно блюдо.')
                return redirect('operator_promotion_edit', pk=promotion.pk)
        messages.success(request, 'Акция обновлена.')
        return redirect('operator_promotion_list')
    return render(
        request,
        'bookings/operator_promotion_form.html',
        {
            'action': 'edit',
            'promotion': promotion,
            'all_dishes': all_dishes,
            'combo_rows': combo_rows,
        },
    )


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_promotion_delete(request, pk):
    promotion = get_object_or_404(Promotion, pk=pk)
    if request.method == 'POST':
        promotion.delete()
        messages.success(request, 'Акция удалена.')
        return redirect('operator_promotion_list')
    return render(request, 'bookings/operator_promotion_confirm_delete.html', {'promotion': promotion})


# ---------- Оператор: жалобы ----------
@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_complaint_list(request):
    if request.method == 'POST':
        cid = request.POST.get('complaint_id')
        new_status = request.POST.get('status')
        if cid and new_status in dict(VenueComplaint.STATUS_CHOICES):
            VenueComplaint.objects.filter(pk=cid).update(status=new_status)
            messages.success(request, 'Статус обновлён.')
        return redirect('operator_complaint_list')
    complaints = VenueComplaint.objects.select_related('user').order_by('-created_at')
    return render(
        request,
        'bookings/operator_complaint_list.html',
        {
            'complaints': complaints,
            'complaint_statuses': VenueComplaint.STATUS_CHOICES,
        },
    )


# ---------- Клиент: история заказов и отзывы ----------
@login_required
@user_passes_test(is_client, login_url='/')
def client_order_list(request):
    orders = Reservation.objects.filter(user=request.user).order_by('-start_time')
    return render(request, 'bookings/client_order_list.html', {'orders': orders})


@login_required
@user_passes_test(is_client, login_url='/')
def client_order_detail(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.select_related('applied_promotion', 'table').prefetch_related(
            'applied_promotion_links__promotion'
        ),
        pk=pk,
        user=request.user,
    )
    lines = reservation.dishes.select_related('dish')
    completed = is_order_completed_for_review(reservation)
    reviewed_line_ids = set(
        DishReview.objects.filter(reservation_dish__reservation=reservation).values_list(
            'reservation_dish_id', flat=True
        )
    )
    line_info = []
    for line in lines:
        line_info.append(
            {
                'line': line,
                'can_review': completed and line.pk not in reviewed_line_ids,
            }
        )
    return render(
        request,
        'bookings/client_order_detail.html',
        {'reservation': reservation, 'line_info': line_info, 'completed': completed},
    )


@login_required
@user_passes_test(is_client, login_url='/')
def client_dish_review_list(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    reviews = DishReview.objects.filter(dish=dish).select_related('user').order_by('-created_at')
    review_stats = reviews.aggregate(avg_rating=Avg('rating'), reviews_count=Count('id'))
    return render(
        request,
        'bookings/client_dish_review_list.html',
        {
            'dish': dish,
            'reviews': reviews,
            'avg_rating': review_stats['avg_rating'],
            'reviews_count': review_stats['reviews_count'],
        },
    )


@login_required
@user_passes_test(is_client, login_url='/')
def client_dish_review_create(request, rid, line_id):
    reservation = get_object_or_404(Reservation, pk=rid, user=request.user)
    line = get_object_or_404(ReservationDish, pk=line_id, reservation=reservation)
    if not is_order_completed_for_review(reservation):
        messages.error(request, 'Отзыв можно оставить только после завершения заказа.')
        return redirect('client_order_detail', pk=reservation.pk)
    if DishReview.objects.filter(reservation_dish=line).exists():
        messages.info(request, 'Отзыв по этой позиции уже оставлен.')
        return redirect('client_order_detail', pk=reservation.pk)
    if request.method == 'POST':
        try:
            rating = int(request.POST.get('rating', '0'))
        except ValueError:
            rating = 0
        comment = request.POST.get('comment', '').strip()
        if rating < 1 or rating > 5:
            messages.error(request, 'Выберите оценку от 1 до 5.')
        else:
            rev = DishReview(
                reservation_dish=line,
                user=request.user,
                dish=line.dish,
                rating=rating,
                comment=comment,
            )
            try:
                rev.full_clean()
                rev.save()
                messages.success(request, 'Спасибо за отзыв!')
                return redirect('client_order_detail', pk=reservation.pk)
            except ValidationError as e:
                messages.error(request, str(e))
    return render(
        request,
        'bookings/client_dish_review_form.html',
        {'reservation': reservation, 'line': line},
    )


# ---------- Клиент: жалобы ----------
@login_required
@user_passes_test(is_client, login_url='/')
def client_complaint_create(request):
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()
        if not subject or not message:
            messages.error(request, 'Заполните тему и текст жалобы.')
        else:
            VenueComplaint.objects.create(user=request.user, subject=subject, message=message)
            messages.success(request, 'Жалоба отправлена. Мы рассмотрим её в ближайшее время.')
            return redirect('client_complaint_list')
    return render(request, 'bookings/client_complaint_form.html')


@login_required
@user_passes_test(is_client, login_url='/')
def client_complaint_list(request):
    items = VenueComplaint.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'bookings/client_complaint_list.html', {'complaints': items})


# ---------- Клиент: новости ----------
@login_required
@user_passes_test(is_client, login_url='/')
def client_news_list(request):
    now = timezone.now()
    items = News.objects.filter(is_published=True, published_at__lte=now).order_by('-published_at')
    return render(request, 'bookings/client_news_list.html', {'news_list': items})


@login_required
@user_passes_test(is_client, login_url='/')
def client_news_detail(request, pk):
    now = timezone.now()
    item = get_object_or_404(News, pk=pk, is_published=True, published_at__lte=now)
    return render(request, 'bookings/client_news_detail.html', {'news_item': item})


@login_required
@user_passes_test(is_client, login_url='/')
def client_promotion_list(request):
    return render(
        request,
        'bookings/client_promotion_list.html',
        {'promotions': get_orderable_promotions()},
    )
