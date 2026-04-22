from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from bookings.models import Dish, Promotion, ReservationDish


def available_quantity_net(dish, exclude_reservation=None):
    """Сколько порций блюда можно ещё заказать (учёт активных бронирований с предзаказом)."""
    if not dish or dish.available_quantity <= 0:
        return 0
    qs = ReservationDish.objects.filter(
        dish=dish,
        reservation__end_time__gte=timezone.now(),
    )
    if exclude_reservation is not None:
        qs = qs.exclude(reservation=exclude_reservation)
    reserved = qs.aggregate(total=Sum("quantity"))["total"] or 0
    return max(0, dish.available_quantity - reserved)


def promotion_is_orderable(promotion):
    """Акцию можно выбрать, если хватает остатков по всем входящим блюдам."""
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False
        for item in items:
            if available_quantity_net(item.dish) < item.min_quantity:
                return False
        return True
    if promotion.kind == Promotion.KIND_SINGLE:
        if not promotion.target_dish_id:
            return False
        return available_quantity_net(promotion.target_dish) >= 1
    return False


def get_orderable_promotions():
    """Активные по срокам акции, по которым сейчас хватает блюд на складе."""
    return [p for p in get_active_promotions() if promotion_is_orderable(p)]


def promotion_fits_menu(promotion, menu_dish_ids):
    """
    Все блюда акции входят в меню на выбранную дату.
    menu_dish_ids — множество или set из id блюд (как у get_menu_dishes_for_date).
    """
    if menu_dish_ids is None:
        return True, None
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False, f'Комбо «{promotion.name}» не содержит позиций.'
        for item in items:
            if item.dish_id not in menu_dish_ids:
                return (
                    False,
                    f'Комбо «{promotion.name}» недоступно в меню на выбранную дату '
                    f'(нет «{item.dish.name}»).',
                )
        return True, None
    if promotion.kind == Promotion.KIND_SINGLE and promotion.target_dish_id:
        if promotion.target_dish_id not in menu_dish_ids:
            return (
                False,
                f'Акция «{promotion.name}» недоступна в меню на выбранную дату.',
            )
    return True, None


def get_active_promotions():
    now = timezone.now()
    return (
        Promotion.objects.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
        .select_related('target_dish')
        .prefetch_related('combo_items__dish')
        .order_by('-valid_from')
    )


def parse_promotion_ids_from_post(post):
    """Уникальные id акций из чекбоксов name=promotion_id (каждая акция не более раза)."""
    seen = set()
    ordered = []
    for x in post.getlist('promotion_id'):
        s = (x or '').strip()
        if not s or s == 'none':
            continue
        try:
            i = int(s)
        except ValueError:
            continue
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    return sorted(ordered)


def parse_dish_quantities_from_post(post):
    quantities = {}
    for key, value in post.items():
        if key.startswith('dish_quantity_'):
            dish_id = int(key.replace('dish_quantity_', ''))
            qty = int(value) if value else 0
            if qty > 0:
                quantities[dish_id] = quantities.get(dish_id, 0) + qty
    return quantities


def combo_implied_quantities(promotion):
    """Состав комбо при выборе ровно одного набора (как в меню fast food)."""
    if promotion.kind != Promotion.KIND_COMBO:
        return {}
    out = {}
    for item in promotion.combo_items.all():
        out[item.dish_id] = out.get(item.dish_id, 0) + item.min_quantity
    return out


def merge_promotion_into_cart(regular_quantities, promotion):
    """
    Объединяет обычную корзину с выбранной акцией (акция один раз на заказ).
    Комбо: набор из акции добавляется к заказу автоматически; порции сверх — из «Обычного меню».
    Одно блюдо: отметка акции сама добавляет одну порцию целевого блюда (не нужно дублировать в меню).
    """
    regular_quantities = dict(regular_quantities)
    if promotion.kind == Promotion.KIND_COMBO:
        combo_map = combo_implied_quantities(promotion)
        if not combo_map:
            return None, 'Комбо-акция не содержит блюд.'
        merged = dict(regular_quantities)
        for did, q in combo_map.items():
            merged[did] = merged.get(did, 0) + q
        return merged, None
    if promotion.kind == Promotion.KIND_SINGLE:
        tid = promotion.target_dish_id
        if not tid:
            return None, 'Акция настроена некорректно.'
        merged = dict(regular_quantities)
        merged[tid] = merged.get(tid, 0) + 1
        return merged, None
    return None, 'Неизвестный тип акции.'


def validate_promotion_application(promotion, dish_qty_map):
    if not promotion.is_active:
        return False, 'Акция неактивна.'
    now = timezone.now()
    if promotion.valid_from > now or promotion.valid_to < now:
        return False, 'Акция не действует в выбранное время.'
    if promotion.kind == Promotion.KIND_SINGLE:
        if not promotion.target_dish_id:
            return False, 'Акция настроена некорректно.'
        if dish_qty_map.get(promotion.target_dish_id, 0) < 1:
            return False, 'Внутренняя ошибка состава заказа по акции на блюдо.'
        return True, None
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False, 'Комбо-акция не содержит блюд.'
        for item in items:
            if dish_qty_map.get(item.dish_id, 0) < item.min_quantity:
                return (
                    False,
                    f'Для акции «{promotion.name}» нужно не меньше {item.min_quantity} × «{item.dish.name}».',
                )
        return True, None
    return False, 'Неизвестный тип акции.'


def validate_merged_cart_stock(dish_qty_map, exclude_reservation=None):
    """
    Проверка остатков для итогового набора порций (включая автоматически добавленные акциями).
    Возвращает текст ошибки или None.
    """
    if not dish_qty_map:
        return None
    ids = [did for did, q in dish_qty_map.items() if q and q > 0]
    if not ids:
        return None
    dishes = {d.pk: d for d in Dish.objects.filter(pk__in=ids)}
    for did, q in dish_qty_map.items():
        if not q or q <= 0:
            continue
        d = dishes.get(did)
        if not d:
            return 'В заказе указано неизвестное блюдо.'
        net = available_quantity_net(d, exclude_reservation=exclude_reservation)
        if q > net:
            return (
                f'«{d.name}»: недостаточно на складе (запрошено {q}, доступно {net}). '
                f'Уменьшите количество в меню или снимите акцию, если не хватает состава комбо.'
            )
    return None


def discount_from_eligible(promotion, eligible):
    """Скидка от суммы eligible (уже ограниченной по правилам акции)."""
    if eligible <= 0:
        return Decimal('0')
    if promotion.discount_type == Promotion.DISCOUNT_PERCENT:
        raw = eligible * (Decimal(promotion.discount_value) / Decimal('100'))
        disc = min(raw, eligible)
    else:
        disc = min(Decimal(promotion.discount_value), eligible)
    return disc.quantize(Decimal('0.01'))


def unit_price_after_single_promo(dish, promotion):
    """Цена одной порции после скидки по акции «одно блюдо» (для отображения в меню)."""
    if promotion.kind != Promotion.KIND_SINGLE:
        return Decimal(dish.price).quantize(Decimal('0.01'))
    eligible = Decimal(dish.price)
    disc = discount_from_eligible(promotion, eligible)
    return (eligible - disc).quantize(Decimal('0.01'))


def compute_promotion_discount(promotion, dish_qty_map, dishes_by_id):
    """
    Скидка только на одну «порцию» акции: для комбо — ровно набор из правил;
    для одного блюда — максимум на 1 шт. Остальные единицы того же блюда — без скидки.
    """
    eligible = Decimal('0')
    if promotion.kind == Promotion.KIND_SINGLE:
        tid = promotion.target_dish_id
        q = dish_qty_map.get(tid, 0)
        d = dishes_by_id.get(tid)
        if d and q:
            promo_units = min(q, 1)
            eligible = Decimal(d.price) * promo_units
    else:
        for item in promotion.combo_items.all():
            d = dishes_by_id.get(item.dish_id)
            if d:
                eligible += Decimal(d.price) * item.min_quantity
    return discount_from_eligible(promotion, eligible)


def compute_per_promotion_discounts(promotions, dish_qty_map, dishes_by_id):
    """
    Скидка по каждой акции один раз. Несколько акций «на одно блюдо» делят порции:
    каждая забирает не более 1 шт., пока есть остаток в заказе.
    Возвращает ([(promotion, amount), ...], сумма).
    """
    allocated = defaultdict(int)
    results = []
    total = Decimal('0')
    for p in sorted(promotions, key=lambda x: x.pk):
        if p.kind == Promotion.KIND_COMBO:
            eligible = Decimal('0')
            for item in p.combo_items.all():
                d = dishes_by_id.get(item.dish_id)
                if d:
                    eligible += Decimal(d.price) * item.min_quantity
            amt = discount_from_eligible(p, eligible)
        else:
            tid = p.target_dish_id
            d = dishes_by_id.get(tid)
            q = dish_qty_map.get(tid, 0)
            if not d or q <= 0:
                amt = Decimal('0')
            else:
                remaining = q - allocated[tid]
                units = min(1, max(0, remaining))
                eligible = Decimal(d.price) * units if units else Decimal('0')
                amt = discount_from_eligible(p, eligible)
                allocated[tid] += units
        results.append((p, amt))
        total += amt
    return results, total.quantize(Decimal('0.01'))


def resolve_promotions_for_checkout_input(
    promotion_ids, regular_qty_map, menu_dish_ids=None, exclude_reservation=None
):
    regular_qty_map = dict(regular_qty_map)
    ids = sorted({int(promotion_id) for promotion_id in (promotion_ids or [])})
    if not ids:
        return [], [], Decimal('0'), None, regular_qty_map
    promos = list(
        Promotion.objects.filter(pk__in=ids)
        .select_related('target_dish')
        .prefetch_related('combo_items__dish')
    )
    if len(promos) != len(ids):
        return [], [], None, 'Указана недействительная акция.', regular_qty_map
    promos.sort(key=lambda p: p.pk)
    for p in promos:
        if not promotion_is_orderable(p):
            return (
                [],
                [],
                None,
                f'Акция «{p.name}» сейчас недоступна: недостаточно порций по складу.',
                regular_qty_map,
            )
    if menu_dish_ids is not None:
        for p in promos:
            ok_menu, menu_err = promotion_fits_menu(p, menu_dish_ids)
            if not ok_menu:
                return [], [], None, menu_err, regular_qty_map
    merged = dict(regular_qty_map)
    for p in promos:
        merged, merr = merge_promotion_into_cart(merged, p)
        if merr:
            return [], [], None, merr, regular_qty_map
    if sum(merged.values()) <= 0:
        return [], [], None, 'Добавьте позиции в заказ или отметьте комбо-акции.', merged
    for p in promos:
        ok, err = validate_promotion_application(p, merged)
        if not ok:
            return [], [], None, err, merged
    stock_err = validate_merged_cart_stock(merged, exclude_reservation=exclude_reservation)
    if stock_err:
        return [], [], None, stock_err, regular_qty_map
    dishes_by_id = {d.pk: d for d in Dish.objects.filter(pk__in=list(merged.keys()))}
    per_promo, total = compute_per_promotion_discounts(promos, merged, dishes_by_id)
    return promos, per_promo, total, None, merged


def resolve_promotions_for_checkout(post, regular_qty_map, menu_dish_ids=None):
    """
    Backward-compatible wrapper for HTML form POST payloads.
    """
    return resolve_promotions_for_checkout_input(
        parse_promotion_ids_from_post(post),
        regular_qty_map,
        menu_dish_ids=menu_dish_ids,
    )


def order_subtotal(dish_qty_map, dishes_by_id):
    total = Decimal('0')
    for did, q in dish_qty_map.items():
        d = dishes_by_id.get(did)
        if d and q:
            total += Decimal(d.price) * q
    return total.quantize(Decimal('0.01'))


def compute_order_totals(dish_qty_map, dishes_by_id, discount_amount):
    subtotal = order_subtotal(dish_qty_map, dishes_by_id)
    disc = discount_amount if discount_amount is not None else Decimal('0')
    total = (subtotal - disc).quantize(Decimal('0.01'))
    if total < 0:
        total = Decimal('0')
    return subtotal, total
