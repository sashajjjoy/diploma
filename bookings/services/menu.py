from django.db.models import Q

from bookings.models import MenuOverride, WeeklyMenu


DEFAULT_WEEKLY_MENU_NAME = "Default weekly menu"


def _get_active_weekly_menu():
    weekly_menu = WeeklyMenu.objects.filter(is_active=True).order_by("name").first()
    if weekly_menu is not None:
        return weekly_menu
    return WeeklyMenu.objects.filter(name=DEFAULT_WEEKLY_MENU_NAME).first()


def get_menu_dishes_for_date(target_date):
    """
    Return an ordered list of dish ids available on the given date.
    Base weekly menu is resolved first, then active overrides are applied
    by priority, date_from, and id.
    """
    weekly_menu = _get_active_weekly_menu()
    if weekly_menu is None:
        return []

    final_dish_ids = []
    day = (
        weekly_menu.days.filter(day_of_week=target_date.weekday(), is_active=True)
        .prefetch_related("items__dish")
        .first()
    )
    if day is not None:
        final_dish_ids = list(
            day.items.order_by("sort_order", "dish__name").values_list("dish_id", flat=True)
        )

    active_overrides = (
        MenuOverride.objects.filter(
            is_active=True,
            date_from__lte=target_date,
        )
        .filter(Q(date_to__isnull=True) | Q(date_to__gte=target_date))
        .filter(Q(weekly_menu=weekly_menu) | Q(weekly_menu__isnull=True))
        .prefetch_related("items")
        .order_by("-priority", "-date_from", "-id")
    )

    for override in active_overrides:
        if override.override_mode == MenuOverride.MODE_REPLACE:
            final_dish_ids = []
        for item in override.items.order_by("order", "dish__name"):
            if item.action == "add":
                if item.dish_id not in final_dish_ids:
                    final_dish_ids.append(item.dish_id)
            elif item.action == "remove":
                final_dish_ids = [dish_id for dish_id in final_dish_ids if dish_id != item.dish_id]

    return final_dish_ids
