from django.urls import path

from .views import (
    AvailableSlotsView,
    ClientOrderDetailView,
    ClientOrderListView,
    ClientReservationDetailView,
    ClientReservationListCreateView,
    ComplaintListCreateView,
    DishListView,
    DishReviewCreateView,
    LoginView,
    LogoutView,
    MeView,
    MenuView,
    OccupiedSlotsView,
    PromotionDetailView,
    PromotionListView,
    PublishedNewsDetailView,
    PublishedNewsListView,
    RefreshView,
)


urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="api_login"),
    path("auth/refresh/", RefreshView.as_view(), name="api_refresh"),
    path("auth/logout/", LogoutView.as_view(), name="api_logout"),
    path("auth/me/", MeView.as_view(), name="api_me"),
    path("news/", PublishedNewsListView.as_view(), name="api_news_list"),
    path("news/<int:pk>/", PublishedNewsDetailView.as_view(), name="api_news_detail"),
    path("promotions/", PromotionListView.as_view(), name="api_promotion_list"),
    path("promotions/<int:pk>/", PromotionDetailView.as_view(), name="api_promotion_detail"),
    path("menu/", MenuView.as_view(), name="api_menu"),
    path("dishes/", DishListView.as_view(), name="api_dishes"),
    path("availability/occupied-slots/", OccupiedSlotsView.as_view(), name="api_occupied_slots"),
    path("availability/available-slots/", AvailableSlotsView.as_view(), name="api_available_slots"),
    path("reservations/", ClientReservationListCreateView.as_view(), name="api_reservations"),
    path("reservations/<int:pk>/", ClientReservationDetailView.as_view(), name="api_reservation_detail"),
    path("orders/", ClientOrderListView.as_view(), name="api_orders"),
    path("orders/<int:pk>/", ClientOrderDetailView.as_view(), name="api_order_detail"),
    path("complaints/", ComplaintListCreateView.as_view(), name="api_complaints"),
    path(
        "orders/<int:reservation_id>/reviews/",
        DishReviewCreateView.as_view(),
        name="api_order_reviews",
    ),
]
