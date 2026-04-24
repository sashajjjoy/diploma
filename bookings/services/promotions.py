from collections import defaultdict
from decimal import Decimal

from django.db import models
from django.db.models import Sum
from django.utils import timezone

from bookings.models import CustomerOrder, Dish, OrderItem, Promotion


def available_quantity_net(dish, exclude_order=None):
    if not dish or dish.available_quantity <= 0:
        return 0
    today = timezone.localdate()
    qs = OrderItem.objects.filter(dish=dish).exclude(order__status=CustomerOrder.STATUS_CANCELLED).filter(
        (models.Q(order__booking__isnull=False) & models.Q(order__booking__end_time__gte=timezone.now()))
        | (models.Q(order__booking__isnull=True) & models.Q(order__scheduled_for__date__gte=today))
    )
    if exclude_order is not None:
        qs = qs.exclude(order=exclude_order)
    reserved = qs.aggregate(total=Sum("quantity"))["total"] or 0
    return max(0, dish.available_quantity - reserved)


def get_active_promotions():
    now = timezone.now()
    return (
        Promotion.objects.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
        .select_related("target_dish")
        .prefetch_related("combo_items__dish")
        .order_by("-valid_from", "pk")
    )


def promotion_base_price(promotion):
    if promotion.kind == Promotion.KIND_SINGLE and promotion.target_dish_id:
        return Decimal(promotion.target_dish.price)
    if promotion.kind == Promotion.KIND_COMBO:
        total = Decimal("0.00")
        for item in promotion.combo_items.all():
            total += Decimal(item.dish.price) * item.min_quantity
        return total
    return Decimal("0.00")


def discount_from_eligible(promotion, eligible_amount):
    if eligible_amount <= 0:
        return Decimal("0.00")
    if promotion.discount_type == Promotion.DISCOUNT_PERCENT:
        discount = eligible_amount * (Decimal(promotion.discount_value) / Decimal("100"))
    else:
        discount = Decimal(promotion.discount_value)
    return min(discount, eligible_amount).quantize(Decimal("0.01"))


def promotion_price_preview(promotion, quantity=1):
    original = (promotion_base_price(promotion) * quantity).quantize(Decimal("0.01"))
    if promotion.discount_type == Promotion.DISCOUNT_PERCENT:
        discount = discount_from_eligible(promotion, original)
    else:
        discount = min(Decimal(promotion.discount_value) * quantity, original).quantize(Decimal("0.01"))
    return {
        "original_price": original,
        "new_price": (original - discount).quantize(Decimal("0.01")),
        "discount_amount": discount,
    }


def unit_price_after_single_promo(dish, promotion):
    preview = promotion_price_preview(promotion, quantity=1)
    return preview["new_price"]


def combo_implied_quantities(promotion, quantity=1):
    if promotion.kind != Promotion.KIND_COMBO:
        return {}
    implied = {}
    for item in promotion.combo_items.all():
        implied[item.dish_id] = implied.get(item.dish_id, 0) + (item.min_quantity * quantity)
    return implied


def promotion_is_orderable(promotion, quantity=1):
    if quantity <= 0:
        return True
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False
        for item in items:
            if available_quantity_net(item.dish) < item.min_quantity * quantity:
                return False
        return True
    if promotion.kind == Promotion.KIND_SINGLE:
        if not promotion.target_dish_id:
            return False
        return available_quantity_net(promotion.target_dish) >= quantity
    return False


def get_orderable_promotions():
    return [promotion for promotion in get_active_promotions() if promotion_is_orderable(promotion, quantity=1)]


def dish_ids_requiring_promotion():
    return {
        promotion.target_dish_id
        for promotion in get_active_promotions()
        if promotion.kind == Promotion.KIND_SINGLE and promotion.target_dish_id
    }


def promotion_fits_menu(promotion, menu_dish_ids):
    if menu_dish_ids is None:
        return True, None
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False, f'Комбо «{promotion.name}» не содержит позиций.'
        for item in items:
            if item.dish_id not in menu_dish_ids:
                return False, f'Комбо «{promotion.name}» недоступно в меню на выбранную дату (нет «{item.dish.name}»).'
        return True, None
    if promotion.kind == Promotion.KIND_SINGLE and promotion.target_dish_id and promotion.target_dish_id not in menu_dish_ids:
        return False, f'Акция «{promotion.name}» недоступна в меню на выбранную дату.'
    return True, None


def parse_promotion_ids_from_post(post):
    return sorted({int(value) for value in post.getlist("promotion_id") if str(value).strip().isdigit()})


def parse_promotion_quantities_from_post(post):
    quantities = {}
    for key, value in post.items():
        if not key.startswith("promotion_quantity_"):
            continue
        suffix = key.replace("promotion_quantity_", "", 1)
        if not suffix.isdigit():
            continue
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            quantities[int(suffix)] = quantity
    if quantities:
        return quantities
    return {promotion_id: 1 for promotion_id in parse_promotion_ids_from_post(post)}


def normalize_promotion_quantities_input(data):
    quantities = {}
    raw_map = data.get("promotion_quantities") or {}
    if isinstance(raw_map, dict):
        for key, value in raw_map.items():
            try:
                promotion_id = int(key)
                quantity = int(value)
            except (TypeError, ValueError):
                continue
            if quantity > 0:
                quantities[promotion_id] = quantity
    if quantities:
        return quantities
    return {int(promotion_id): 1 for promotion_id in (data.get("promotion_ids") or [])}


def parse_dish_quantities_from_post(post):
    quantities = {}
    for key, value in post.items():
        if not key.startswith("dish_quantity_"):
            continue
        suffix = key.replace("dish_quantity_", "", 1)
        if not suffix.isdigit():
            continue
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            quantities[int(suffix)] = quantities.get(int(suffix), 0) + quantity
    return quantities


def merge_promotion_into_cart(regular_quantities, promotion, quantity):
    merged = dict(regular_quantities)
    if quantity <= 0:
        return merged, None
    if promotion.kind == Promotion.KIND_COMBO:
        combo_map = combo_implied_quantities(promotion, quantity=quantity)
        if not combo_map:
            return None, "Комбо-акция не содержит блюд."
        for dish_id, implied_qty in combo_map.items():
            merged[dish_id] = merged.get(dish_id, 0) + implied_qty
        return merged, None
    if promotion.kind == Promotion.KIND_SINGLE:
        if not promotion.target_dish_id:
            return None, "Акция настроена некорректно."
        merged[promotion.target_dish_id] = merged.get(promotion.target_dish_id, 0) + quantity
        return merged, None
    return None, "Неизвестный тип акции."


def validate_promotion_application(promotion, quantity, dish_qty_map):
    if quantity <= 0:
        return True, None
    if not promotion.is_active:
        return False, "Акция неактивна."
    now = timezone.now()
    if promotion.valid_from > now or promotion.valid_to < now:
        return False, "Акция не действует в выбранное время."
    if promotion.kind == Promotion.KIND_SINGLE:
        if dish_qty_map.get(promotion.target_dish_id, 0) < quantity:
            return False, "В заказе недостаточно порций для применения акции."
        return True, None
    if promotion.kind == Promotion.KIND_COMBO:
        items = list(promotion.combo_items.all())
        if not items:
            return False, "Комбо-акция не содержит блюд."
        for item in items:
            required = item.min_quantity * quantity
            if dish_qty_map.get(item.dish_id, 0) < required:
                return False, f'Для акции «{promotion.name}» нужно не меньше {required} × «{item.dish.name}».'
        return True, None
    return False, "Неизвестный тип акции."


def validate_merged_cart_stock(dish_qty_map, exclude_order=None):
    if not dish_qty_map:
        return None
    dishes = {dish.pk: dish for dish in Dish.objects.filter(pk__in=list(dish_qty_map.keys()))}
    for dish_id, quantity in dish_qty_map.items():
        if quantity <= 0:
            continue
        dish = dishes.get(dish_id)
        if not dish:
            return "В заказе указано неизвестное блюдо."
        available = available_quantity_net(dish, exclude_order=exclude_order)
        if quantity > available:
            return f'«{dish.name}»: недостаточно на складе (запрошено {quantity}, доступно {available}).'
    return None


def compute_per_promotion_discounts(promotions_with_qty):
    rows = []
    total_discount = Decimal("0.00")
    for promotion, quantity in promotions_with_qty:
        if quantity <= 0:
            continue
        preview = promotion_price_preview(promotion, quantity=quantity)
        row = {
            "promotion": promotion,
            "quantity": quantity,
            "original_amount": preview["original_price"],
            "discount_amount": preview["discount_amount"],
            "discounted_amount": preview["new_price"],
        }
        rows.append(row)
        total_discount += row["discount_amount"]
    return rows, total_discount.quantize(Decimal("0.01"))


def resolve_promotions_for_checkout_input(promotion_quantities, regular_qty_map, menu_dish_ids=None, exclude_order=None):
    regular_qty_map = dict(regular_qty_map)
    normalized_quantities = {int(pid): int(qty) for pid, qty in (promotion_quantities or {}).items() if int(qty) > 0}
    if not normalized_quantities:
        return [], [], Decimal("0.00"), None, regular_qty_map

    promotion_ids = sorted(normalized_quantities.keys())
    promotions = list(
        Promotion.objects.filter(pk__in=promotion_ids)
        .select_related("target_dish")
        .prefetch_related("combo_items__dish")
    )
    if len(promotions) != len(promotion_ids):
        return [], [], None, "Указана недействительная акция.", regular_qty_map

    promotions.sort(key=lambda promotion: promotion.pk)
    promotions_with_qty = []
    for promotion in promotions:
        quantity = normalized_quantities.get(promotion.pk, 0)
        promotions_with_qty.append((promotion, quantity))
        if quantity <= 0:
            continue
        if not promotion_is_orderable(promotion, quantity=quantity):
            return [], [], None, f'Акция «{promotion.name}» сейчас недоступна: недостаточно порций по складу.', regular_qty_map
        if menu_dish_ids is not None:
            ok_menu, menu_error = promotion_fits_menu(promotion, menu_dish_ids)
            if not ok_menu:
                return [], [], None, menu_error, regular_qty_map

    merged = dict(regular_qty_map)
    for promotion, quantity in promotions_with_qty:
        merged, merge_error = merge_promotion_into_cart(merged, promotion, quantity)
        if merge_error:
            return [], [], None, merge_error, regular_qty_map

    if sum(merged.values()) <= 0:
        return [], [], None, "Добавьте позиции в заказ или выберите акции.", merged

    for promotion, quantity in promotions_with_qty:
        ok, error = validate_promotion_application(promotion, quantity, merged)
        if not ok:
            return [], [], None, error, merged

    stock_error = validate_merged_cart_stock(merged, exclude_order=exclude_order)
    if stock_error:
        return [], [], None, stock_error, regular_qty_map

    per_promo_rows, total_discount = compute_per_promotion_discounts(promotions_with_qty)
    return promotions, per_promo_rows, total_discount, None, merged


def resolve_promotions_for_checkout(post, regular_qty_map, menu_dish_ids=None):
    return resolve_promotions_for_checkout_input(
        parse_promotion_quantities_from_post(post),
        regular_qty_map,
        menu_dish_ids=menu_dish_ids,
    )


def order_subtotal(dish_qty_map, dishes_by_id):
    total = Decimal("0.00")
    for dish_id, quantity in dish_qty_map.items():
        dish = dishes_by_id.get(dish_id)
        if dish and quantity:
            total += Decimal(dish.price) * quantity
    return total.quantize(Decimal("0.01"))


def compute_order_totals(dish_qty_map, dishes_by_id, discount_amount):
    subtotal = order_subtotal(dish_qty_map, dishes_by_id)
    discount = discount_amount if discount_amount is not None else Decimal("0.00")
    total = (subtotal - discount).quantize(Decimal("0.01"))
    if total < 0:
        total = Decimal("0.00")
    return subtotal, total
