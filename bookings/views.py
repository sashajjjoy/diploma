from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
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
    РџРѕР»СѓС‡Р°РµС‚ СЃРїРёСЃРѕРє Р±Р»СЋРґ, РґРѕСЃС‚СѓРїРЅС‹С… РІ РјРµРЅСЋ РЅР° СѓРєР°Р·Р°РЅРЅСѓСЋ РґР°С‚Сѓ.
    РЈС‡РёС‚С‹РІР°РµС‚ РµР¶РµРЅРµРґРµР»СЊРЅРѕРµ РјРµРЅСЋ Рё РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёСЏ.
    
    Args:
        target_date: date РѕР±СЉРµРєС‚
        
    Returns:
        set: РјРЅРѕР¶РµСЃС‚РІРѕ ID Р±Р»СЋРґ, РґРѕСЃС‚СѓРїРЅС‹С… РІ РјРµРЅСЋ РЅР° СЌС‚Сѓ РґР°С‚Сѓ
    """
    day_of_week = target_date.weekday()  # 0=РїРѕРЅРµРґРµР»СЊРЅРёРє, 6=РІРѕСЃРєСЂРµСЃРµРЅСЊРµ
    
    weekly_dishes = []
    try:
        day_settings = WeeklyMenuDaySettings.objects.get(day_of_week=day_of_week, is_active=True)
        weekly_menu_items = WeeklyMenuItem.objects.filter(day_settings=day_settings).order_by(
            'order', 'dish__name'
        )
        weekly_dishes = [item.dish for item in weekly_menu_items]
    except WeeklyMenuDaySettings.DoesNotExist:
        pass
    
    active_overrides = MenuOverride.objects.filter(
        is_active=True,
        date_from__lte=target_date
    ).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=target_date)
    ).order_by('-date_from')
    
    final_dishes = list(weekly_dishes)  # РќР°С‡РёРЅР°РµРј СЃ РµР¶РµРЅРµРґРµР»СЊРЅРѕРіРѕ РјРµРЅСЋ
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
    РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРё РІС‹Р±РёСЂР°РµС‚ РїРѕРґС…РѕРґСЏС‰РёР№ СЃС‚РѕР»РёРє РґР»СЏ Р±СЂРѕРЅРёСЂРѕРІР°РЅРёСЏ.
    Р’С‹Р±РёСЂР°РµС‚ СЃС‚РѕР»РёРє СЃ РЅР°РёРјРµРЅСЊС€РёРј РґРѕСЃС‚Р°С‚РѕС‡РЅС‹Рј РєРѕР»РёС‡РµСЃС‚РІРѕРј РјРµСЃС‚ РёР· РґРѕСЃС‚СѓРїРЅС‹С….
    
    Args:
        guests_count: РљРѕР»РёС‡РµСЃС‚РІРѕ РіРѕСЃС‚РµР№
        start_datetime: РќР°С‡Р°Р»Рѕ Р±СЂРѕРЅРёСЂРѕРІР°РЅРёСЏ (aware datetime)
        end_datetime: РћРєРѕРЅС‡Р°РЅРёРµ Р±СЂРѕРЅРёСЂРѕРІР°РЅРёСЏ (aware datetime)
        exclude_reservation_id: ID Р±СЂРѕРЅРёСЂРѕРІР°РЅРёСЏ РґР»СЏ РёСЃРєР»СЋС‡РµРЅРёСЏ (РїСЂРё СЂРµРґР°РєС‚РёСЂРѕРІР°РЅРёРё)
    
    Returns:
        Table РёР»Рё None, РµСЃР»Рё РїРѕРґС…РѕРґСЏС‰РёР№ СЃС‚РѕР»РёРє РЅРµ РЅР°Р№РґРµРЅ
    """
    suitable_tables = Table.objects.filter(seats__gte=guests_count).order_by('seats')
    
    for table in suitable_tables:
        overlapping_query = Booking.objects.exclude(status=Booking.STATUS_CANCELLED).filter(
            table=table
        ).filter(
            Q(start_time__lt=end_datetime) & Q(end_time__gt=start_datetime)
        )
        
        if exclude_reservation_id:
            overlapping_query = overlapping_query.exclude(pk=exclude_reservation_id)
        
        if not overlapping_query.exists():
            return table
    
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
    """Р”РѕСЃС‚СѓРї Рє РєР°Р±РёРЅРµС‚Сѓ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° РІ РїСЂРёР»РѕР¶РµРЅРёРё."""
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
        UserProfile.objects.get_or_create(
            user=request.user,
            defaults={'role': 'client'}
        )
    return redirect('home')

LOW_STOCK_THRESHOLD = 5



@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_tables(request):
    """РЎРїРёСЃРѕРє СЃС‚РѕР»РёРєРѕРІ РґР»СЏ РѕРїРµСЂР°С‚РѕСЂР°"""
    tables = Table.objects.all().order_by('table_number')
    
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
    """РЎРѕР·РґР°РЅРёРµ СЃС‚РѕР»РёРєР°"""
    if request.method == 'POST':
        table_number = request.POST.get('table_number')
        seats = request.POST.get('seats')
        
        try:
            table = Table.objects.create(
                table_number=table_number,
                seats=int(seats)
            )
            messages.success(request, f'РЎС‚РѕР»РёРє в„–{table_number} СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅ!')
            return redirect('operator_table_detail', pk=table.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕР·РґР°РЅРёРё СЃС‚РѕР»РёРєР°: {str(e)}')
    
    return render(request, 'bookings/operator_table_form.html', {'action': 'create'})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_table_detail(request, pk):
    """Р”РµС‚Р°Р»СЊРЅР°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ Рѕ СЃС‚РѕР»РёРєРµ"""
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
    """Р РµРґР°РєС‚РёСЂРѕРІР°РЅРёРµ СЃС‚РѕР»РёРєР°"""
    table = get_object_or_404(Table, pk=pk)
    
    if request.method == 'POST':
        table.table_number = request.POST.get('table_number')
        table.seats = int(request.POST.get('seats'))
        try:
            table.full_clean()
            table.save()
            messages.success(request, 'РЎС‚РѕР»РёРє СѓСЃРїРµС€РЅРѕ РёР·РјРµРЅРµРЅ!')
            return redirect('operator_table_detail', pk=table.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё: {str(e)}')
    
    context = {
        'table': table,
        'action': 'edit',
    }
    return render(request, 'bookings/operator_table_form.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_table_delete(request, pk):
    """РЈРґР°Р»РµРЅРёРµ СЃС‚РѕР»РёРєР°"""
    table = get_object_or_404(Table, pk=pk)
    
    if request.method == 'POST':
        try:
            table.delete()
            messages.success(request, 'РЎС‚РѕР»РёРє СѓСЃРїРµС€РЅРѕ СѓРґР°Р»РµРЅ!')
            return redirect('operator_tables')
        except Exception as e:
            messages.error(request, f'РќРµРІРѕР·РјРѕР¶РЅРѕ СѓРґР°Р»РёС‚СЊ СЃС‚РѕР»РёРє: {str(e)}')
            return redirect('operator_table_detail', pk=table.pk)
    
    context = {'table': table}
    return render(request, 'bookings/operator_table_confirm_delete.html', context)



@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_dishes(request):
    """РЎРїРёСЃРѕРє Р±Р»СЋРґ РґР»СЏ РѕРїРµСЂР°С‚РѕСЂР°"""
    dishes = Dish.objects.all().order_by('name')
    
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
    """РЎРѕР·РґР°РЅРёРµ Р±Р»СЋРґР°"""
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
            messages.success(request, f'Р‘Р»СЋРґРѕ "{name}" СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅРѕ!')
            return redirect('operator_dish_detail', pk=dish.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕР·РґР°РЅРёРё Р±Р»СЋРґР°: {str(e)}')
    
    return render(request, 'bookings/operator_dish_form.html', {'action': 'create'})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_dish_detail(request, pk):
    """Р”РµС‚Р°Р»СЊРЅР°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ Рѕ Р±Р»СЋРґРµ"""
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
    """Р РµРґР°РєС‚РёСЂРѕРІР°РЅРёРµ Р±Р»СЋРґР°"""
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
            messages.success(request, 'Р‘Р»СЋРґРѕ СѓСЃРїРµС€РЅРѕ РёР·РјРµРЅРµРЅРѕ!')
            return redirect('operator_dish_detail', pk=dish.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё: {str(e)}')
    
    context = {
        'dish': dish,
        'action': 'edit',
    }
    return render(request, 'bookings/operator_dish_form.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_dish_delete(request, pk):
    """РЈРґР°Р»РµРЅРёРµ Р±Р»СЋРґР°"""
    dish = get_object_or_404(Dish, pk=pk)
    
    if request.method == 'POST':
        try:
            dish.delete()
            messages.success(request, 'Р‘Р»СЋРґРѕ СѓСЃРїРµС€РЅРѕ СѓРґР°Р»РµРЅРѕ!')
            return redirect('operator_dishes')
        except Exception as e:
            messages.error(request, f'РќРµРІРѕР·РјРѕР¶РЅРѕ СѓРґР°Р»РёС‚СЊ Р±Р»СЋРґРѕ: {str(e)}')
            return redirect('operator_dish_detail', pk=dish.pk)
    
    context = {'dish': dish}
    return render(request, 'bookings/operator_dish_confirm_delete.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menus(request):
    """РЎРїРёСЃРѕРє РµР¶РµРЅРµРґРµР»СЊРЅС‹С… РјРµРЅСЋ РґР»СЏ РѕРїРµСЂР°С‚РѕСЂР° (С‚РѕР»СЊРєРѕ СЂР°Р±РѕС‡РёРµ РґРЅРё)"""
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
    """Просмотр меню на выбранную дату."""
    date_str = request.GET.get('date')
    
    if not date_str:
        messages.error(request, 'Не указана дата.')
        return redirect('operator_menus')
    
    try:
        from datetime import datetime
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_of_week = selected_date.weekday()  # 0=понедельник, 6=воскресенье
        
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
        
        active_overrides = MenuOverride.objects.filter(
            is_active=True,
            date_from__lte=selected_date
        ).filter(
            Q(date_to__isnull=True) | Q(date_to__gte=selected_date)
        ).order_by('-date_from')
        
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
        messages.error(request, 'Неверный формат даты.')
        return redirect('operator_menus')
    except Exception as e:
        messages.error(request, f'Ошибка при получении меню: {str(e)}')
        return redirect('operator_menus')


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menus_create_all(request):
    """Создание меню на рабочие дни недели (понедельник-пятница)."""
    all_dishes = Dish.objects.all().order_by('name')
    
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
        for day_data in working_days_data:
            day = day_data['day']
            menu = day_data['menu']
            menu.is_active = request.POST.get(f'day_{day}_active') == 'on'
            menu.save()
            
            WeeklyMenuItem.objects.filter(day_settings=menu).delete()
            
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
        
        messages.success(request, 'РњРµРЅСЋ РЅР° СЂР°Р±РѕС‡РёРµ РґРЅРё СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅРѕ!')
        return redirect('operator_menus')
    
    context = {
        'all_dishes': all_dishes,
        'working_days_data': working_days_data,
    }
    return render(request, 'bookings/operator_menus_create_all.html', context)


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menu_edit(request, day_of_week):
    """Р РµРґР°РєС‚РёСЂРѕРІР°РЅРёРµ РјРµРЅСЋ РЅР° РґРµРЅСЊ РЅРµРґРµР»Рё"""
    menu, created = WeeklyMenuDaySettings.objects.get_or_create(day_of_week=day_of_week)
    all_dishes = Dish.objects.all().order_by('name')
    menu_items = WeeklyMenuItem.objects.filter(day_settings=menu).order_by('order', 'dish__name')
    
    if request.method == 'POST':
        menu.is_active = request.POST.get('is_active') == 'on'
        menu.save()
        
        WeeklyMenuItem.objects.filter(day_settings=menu).delete()
        
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
        
        messages.success(request, f'РњРµРЅСЋ РЅР° {menu.get_day_of_week_display()} СѓСЃРїРµС€РЅРѕ РѕР±РЅРѕРІР»РµРЅРѕ!')
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
    """РЎРѕР·РґР°РЅРёРµ РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёСЏ РјРµРЅСЋ"""
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
            
            messages.success(request, 'РџРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёРµ РјРµРЅСЋ СѓСЃРїРµС€РЅРѕ СЃРѕР·РґР°РЅРѕ!')
            return redirect('operator_menu_override_detail', pk=override.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕР·РґР°РЅРёРё РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёСЏ: {str(e)}')
    
    context = {
        'all_dishes': all_dishes,
    }
    return render(request, 'bookings/operator_menu_override_form.html', {'action': 'create', 'all_dishes': all_dishes})


@login_required
@user_passes_test(is_operator_or_admin, login_url='/')
def operator_menu_override_detail(request, pk):
    """Р”РµС‚Р°Р»СЊРЅР°СЏ РёРЅС„РѕСЂРјР°С†РёСЏ Рѕ РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёРё РјРµРЅСЋ"""
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
    """Р РµРґР°РєС‚РёСЂРѕРІР°РЅРёРµ РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёСЏ РјРµРЅСЋ"""
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
            
            MenuOverrideItem.objects.filter(override=override).delete()
            
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
            
            messages.success(request, 'РџРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёРµ РјРµРЅСЋ СѓСЃРїРµС€РЅРѕ РѕР±РЅРѕРІР»РµРЅРѕ!')
            return redirect('operator_menu_override_detail', pk=override.pk)
        except Exception as e:
            messages.error(request, f'РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё: {str(e)}')
    
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
    """РЈРґР°Р»РµРЅРёРµ РїРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёСЏ РјРµРЅСЋ"""
    override = get_object_or_404(MenuOverride, pk=pk)
    
    if request.method == 'POST':
        override.delete()
        messages.success(request, 'РџРµСЂРµРѕРїСЂРµРґРµР»РµРЅРёРµ РјРµРЅСЋ СѓСЃРїРµС€РЅРѕ СѓРґР°Р»РµРЅРѕ!')
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

@login_required
@user_passes_test(is_client, login_url='/')
def client_complaint_create(request):
    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        message = request.POST.get('message', '').strip()
        if not subject or not message:
            messages.error(request, 'Р—Р°РїРѕР»РЅРёС‚Рµ С‚РµРјСѓ Рё С‚РµРєСЃС‚ Р¶Р°Р»РѕР±С‹.')
        else:
            VenueComplaint.objects.create(user=request.user, subject=subject, message=message)
            messages.success(request, 'Р–Р°Р»РѕР±Р° РѕС‚РїСЂР°РІР»РµРЅР°. РњС‹ СЂР°СЃСЃРјРѕС‚СЂРёРј РµС‘ РІ Р±Р»РёР¶Р°Р№С€РµРµ РІСЂРµРјСЏ.')
            return redirect('client_complaint_list')
    return render(request, 'bookings/client_complaint_form.html')


@login_required
@user_passes_test(is_client, login_url='/')
def client_complaint_list(request):
    items = VenueComplaint.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'bookings/client_complaint_list.html', {'complaints': items})


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



