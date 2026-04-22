from django.db.models import Q

from bookings.models import MenuOverride, MenuOverrideItem, WeeklyMenuDaySettings, WeeklyMenuItem


def get_menu_dishes_for_date(target_date):
    """
    Return a set of dish ids available in the menu on the given date.
    Weekly menu is used as a base and then active overrides are applied.
    """
    day_of_week = target_date.weekday()

    weekly_dishes = []
    try:
        day_settings = WeeklyMenuDaySettings.objects.get(day_of_week=day_of_week, is_active=True)
        weekly_menu_items = WeeklyMenuItem.objects.filter(day_settings=day_settings).order_by(
            "order", "dish__name"
        )
        weekly_dishes = [item.dish for item in weekly_menu_items]
    except WeeklyMenuDaySettings.DoesNotExist:
        pass

    active_overrides = MenuOverride.objects.filter(
        is_active=True,
        date_from__lte=target_date,
    ).filter(Q(date_to__isnull=True) | Q(date_to__gte=target_date)).order_by("-date_from")

    final_dishes = list(weekly_dishes)
    dish_set = {dish.id for dish in final_dishes}

    for override in active_overrides:
        override_items = MenuOverrideItem.objects.filter(override=override).order_by(
            "order", "dish__name"
        )
        for item in override_items:
            if item.action == "add":
                if item.dish.id not in dish_set:
                    final_dishes.append(item.dish)
                    dish_set.add(item.dish.id)
            elif item.action == "remove":
                final_dishes = [dish for dish in final_dishes if dish.id != item.dish.id]
                dish_set.discard(item.dish.id)

    return dish_set
