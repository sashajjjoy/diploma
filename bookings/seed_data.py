"""
Очистка данных (кроме Table и Dish) и заполнение демо-данными.
Минимум записей в контентных таблицах — MIN_ROWS (типичное требование курсовых).
"""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from bookings.models import (
    Dish,
    DishReview,
    MenuOverride,
    MenuOverrideItem,
    News,
    Promotion,
    PromotionComboItem,
    Reservation,
    ReservationAppliedPromotion,
    ReservationDish,
    Table,
    UserProfile,
    VenueComplaint,
    WeeklyMenuDaySettings,
    WeeklyMenuItem,
)

User = get_user_model()

# Не меньше стольких строк там, где это осмысленно (новости, жалобы, брони и т.д.)
MIN_ROWS = 10


def clear_all_except_table_dish() -> None:
    Session.objects.all().delete()
    DishReview.objects.all().delete()
    ReservationAppliedPromotion.objects.all().delete()
    ReservationDish.objects.all().delete()
    PromotionComboItem.objects.all().delete()
    Reservation.objects.all().delete()
    Promotion.objects.all().delete()
    VenueComplaint.objects.all().delete()
    News.objects.all().delete()
    MenuOverrideItem.objects.all().delete()
    MenuOverride.objects.all().delete()
    WeeklyMenuItem.objects.all().delete()
    WeeklyMenuDaySettings.objects.all().delete()
    UserProfile.objects.all().delete()
    User.objects.all().delete()


def _ensure_tables_and_dishes():
    tables = list(Table.objects.order_by("id"))
    dishes = list(Dish.objects.order_by("id"))
    if not tables:
        raise RuntimeError("В базе нет столиков (Table). Добавьте записи или не очищайте эту таблицу.")
    if not dishes:
        raise RuntimeError("В базе нет блюд (Dish). Добавьте записи или не очищайте эту таблицу.")
    return tables, dishes


def _make_users():
    users = []
    for i in range(MIN_ROWS):
        u = User.objects.create_user(
            username=f"client{i + 1}",
            email=f"client{i + 1}@example.com",
            password="client123",
            first_name=f"Клиент{i + 1}",
            last_name="Тестов",
        )
        UserProfile.objects.create(user=u, role="client", phone=f"+7900123456{i % 10}")
        users.append(u)

    op = User.objects.create_user(
        username="operator1",
        email="operator1@example.com",
        password="operator123",
        first_name="Пётр",
        last_name="Операторов",
    )
    UserProfile.objects.create(user=op, role="operator")

    ad = User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password="admin123",
        first_name="Админ",
        last_name="",
        is_staff=True,
        is_superuser=True,
    )
    UserProfile.objects.create(user=ad, role="admin")
    return users, op, ad


def _reservation_slot_past(index: int, tables: list[Table]):
    """Уникальные слоты без пересечений по столику."""
    n = len(tables)
    day_offset = index // n + 1
    table_idx = index % n
    hour = 9 + (index % 6) * 2
    d = timezone.localdate() - timedelta(days=day_offset)
    st = timezone.make_aware(datetime.combine(d, time(hour, 0)))
    et = st + timedelta(hours=1, minutes=30)
    t = tables[table_idx]
    guests = min(2, t.seats)
    return t, st, et, guests


def _future_dates_within_two_working_days(need: int):
    """Даты (date), для которых до начала дня ≤ 2 рабочих дней (как в Reservation.clean)."""
    out = []
    today = timezone.localdate()
    for add in range(0, 21):
        d = today + timedelta(days=add)
        if d.weekday() >= 5:
            continue
        noon = timezone.make_aware(datetime.combine(d, time(12, 0)))
        r = Reservation(start_time=noon)
        if r.get_working_days_until(noon) <= 2:
            out.append(d)
        if len(out) >= need:
            break
    return out


def _reservation_slot_future(index: int, tables: list[Table], future_dates: list):
    n = len(tables)
    d = future_dates[index % len(future_dates)]
    hour = 10 + (index % 5) * 2
    table_idx = (index + (index // len(future_dates))) % n
    st = timezone.make_aware(datetime.combine(d, time(hour, 0)))
    et = st + timedelta(hours=1, minutes=30)
    t = tables[table_idx]
    guests = min(2 + (index % 2), t.seats)
    return t, st, et, guests


def seed_demo_data() -> dict[str, int]:
    tables, dishes = _ensure_tables_and_dishes()
    client_users, _op, _ad = _make_users()
    clients_only = client_users[:MIN_ROWS]

    for i in range(MIN_ROWS):
        t, st, et, g = _reservation_slot_past(i, tables)
        Reservation.objects.create(
            user=clients_only[i % len(clients_only)],
            table=t,
            guests_count=g,
            start_time=st,
            end_time=et,
        )

    future_dates = _future_dates_within_two_working_days(max(MIN_ROWS, 5))
    if not future_dates:
        future_dates = [timezone.localdate() + timedelta(days=1)]

    for i in range(MIN_ROWS):
        t, st, et, g = _reservation_slot_future(i, tables, future_dates)
        Reservation.objects.create(
            user=clients_only[i % len(clients_only)],
            table=t,
            guests_count=g,
            start_time=st,
            end_time=et,
        )

    all_res = list(Reservation.objects.order_by("start_time"))
    past_res = [r for r in all_res if r.start_time < timezone.now()]
    future_res = [r for r in all_res if r.start_time >= timezone.now()]

    for i, res in enumerate(all_res[: max(MIN_ROWS * 2, MIN_ROWS)]):
        d1 = dishes[i % len(dishes)]
        d2 = dishes[(i + 1) % len(dishes)]
        ReservationDish.objects.get_or_create(
            reservation=res,
            dish=d1,
            defaults={"quantity": 1 + (i % 2)},
        )
        if i % 2 == 0 and d2.id != d1.id:
            ReservationDish.objects.get_or_create(
                reservation=res,
                dish=d2,
                defaults={"quantity": 1},
            )

    day_settings = []
    for dow in range(7):
        ds, _ = WeeklyMenuDaySettings.objects.get_or_create(
            day_of_week=dow,
            defaults={"is_active": dow < 5},
        )
        day_settings.append(ds)

    w_items = []
    for order in range(MIN_ROWS):
        ds = day_settings[order % 7]
        dish = dishes[(order // 7) % len(dishes)]
        w_items.append(WeeklyMenuItem(day_settings=ds, dish=dish, order=order))
    WeeklyMenuItem.objects.bulk_create(w_items)

    today = timezone.localdate()
    overrides = []
    n_per_ov_items = min(3, len(dishes))
    n_ov = max(3, math.ceil(MIN_ROWS / n_per_ov_items))
    for k in range(n_ov):
        overrides.append(
            MenuOverride(
                date_from=today + timedelta(days=k * 7),
                date_to=today + timedelta(days=k * 7 + 2),
                is_active=True,
            )
        )
    MenuOverride.objects.bulk_create(overrides)

    moi = []
    for i, ov in enumerate(MenuOverride.objects.order_by("id")):
        for j in range(n_per_ov_items):
            moi.append(
                MenuOverrideItem(
                    override=ov,
                    dish=dishes[(i + j) % len(dishes)],
                    action="add" if j % 2 == 0 else "remove",
                    order=j,
                )
            )
    MenuOverrideItem.objects.bulk_create(moi)

    news_list = []
    for i in range(MIN_ROWS):
        pub = timezone.now() - timedelta(days=MIN_ROWS - i)
        news_list.append(
            News(
                title=f"Новость корпоративной столовой №{i + 1}",
                summary=f"Кратко: обновление меню и график питания ({i + 1}).",
                body="Полный текст новости для демонстрации ленты. Столовая работает в штатном режиме.",
                published_at=pub,
                is_published=True,
            )
        )
    News.objects.bulk_create(news_list)

    complaints = []
    for i in range(MIN_ROWS):
        complaints.append(
            VenueComplaint(
                user=clients_only[i % len(clients_only)],
                subject=f"Замечание по сервису #{i + 1}",
                message=f"Текст обращения {i + 1}: температура в зале, очередь на кассе и т.п.",
                status=["new", "seen", "closed"][i % 3],
            )
        )
    VenueComplaint.objects.bulk_create(complaints)

    now = timezone.now()
    promos = []
    for i in range(MIN_ROWS // 2):
        promos.append(
            Promotion(
                name=f"Скидка на блюдо №{i + 1}",
                description=f"Специальное предложение на выбранную позицию ({i + 1}).",
                kind=Promotion.KIND_SINGLE,
                discount_type=Promotion.DISCOUNT_PERCENT,
                discount_value=Decimal("10") + i,
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=90),
                is_active=True,
                target_dish=dishes[i % len(dishes)],
            )
        )
    for i in range(MIN_ROWS - len(promos)):
        promos.append(
            Promotion(
                name=f"Комбо-набор №{i + 1}",
                description=f"Закажите набор блюд со скидкой ({i + 1}).",
                kind=Promotion.KIND_COMBO,
                discount_type=Promotion.DISCOUNT_FIXED_OFF,
                discount_value=Decimal("50") + i * 5,
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=60),
                is_active=True,
                target_dish=None,
            )
        )
    Promotion.objects.bulk_create(promos)

    combo_promos = list(Promotion.objects.filter(kind=Promotion.KIND_COMBO))
    combo_items = []
    ncombo = min(2, len(dishes))
    for p in combo_promos:
        for j in range(ncombo):
            combo_items.append(
                PromotionComboItem(
                    promotion=p,
                    dish=dishes[j],
                    min_quantity=1 + j,
                )
            )
    PromotionComboItem.objects.bulk_create(combo_items)

    single_promos = list(Promotion.objects.filter(kind=Promotion.KIND_SINGLE))
    for i, res in enumerate(future_res[: min(5, len(future_res))]):
        if not single_promos:
            break
        pr = single_promos[i % len(single_promos)]
        ReservationAppliedPromotion.objects.get_or_create(
            reservation=res,
            promotion=pr,
            defaults={"discount_amount": Decimal("15.00") + i},
        )
        res.applied_promotion = pr
        res.promotion_discount_total = Decimal("15.00") + i
        res.save(update_fields=["applied_promotion", "promotion_discount_total"])

    rds = list(
        ReservationDish.objects.filter(reservation__in=past_res).select_related("reservation", "dish")[:MIN_ROWS]
    )
    reviews = []
    for i, rd in enumerate(rds):
        reviews.append(
            DishReview(
                reservation_dish=rd,
                user=rd.reservation.user,
                dish=rd.dish,
                rating=3 + (i % 3),
                comment=f"Демо-отзыв {i + 1}: вкус и порция.",
            )
        )
    DishReview.objects.bulk_create(reviews)

    return {
        "Table": Table.objects.count(),
        "Dish": Dish.objects.count(),
        "User": User.objects.count(),
        "UserProfile": UserProfile.objects.count(),
        "Reservation": Reservation.objects.count(),
        "ReservationDish": ReservationDish.objects.count(),
        "WeeklyMenuDaySettings": WeeklyMenuDaySettings.objects.count(),
        "WeeklyMenuItem": WeeklyMenuItem.objects.count(),
        "MenuOverride": MenuOverride.objects.count(),
        "MenuOverrideItem": MenuOverrideItem.objects.count(),
        "News": News.objects.count(),
        "VenueComplaint": VenueComplaint.objects.count(),
        "Promotion": Promotion.objects.count(),
        "PromotionComboItem": PromotionComboItem.objects.count(),
        "ReservationAppliedPromotion": ReservationAppliedPromotion.objects.count(),
        "DishReview": DishReview.objects.count(),
    }


@transaction.atomic
def run_reseed() -> dict[str, int]:
    clear_all_except_table_dish()
    return seed_demo_data()
