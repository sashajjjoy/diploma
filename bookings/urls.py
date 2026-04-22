from django.urls import path
from . import views

urlpatterns = [
    # Общие
    path('', views.dashboard, name='dashboard'),
    path('admin/cabinet/', views.admin_cabinet, name='admin_cabinet'),
    
    # API для получения занятых временных слотов
    path('api/occupied-slots/', views.get_occupied_time_slots, name='get_occupied_time_slots'),
    path('api/available-slots/', views.check_available_time_slots, name='check_available_time_slots'),
    
    # Бронирования клиента
    path('reservations/create/', views.reservation_create, name='reservation_create'),
    path('client/reservations/<int:pk>/', views.reservation_detail, name='reservation_detail'),
    path('client/reservations/<int:pk>/edit/', views.reservation_edit, name='reservation_edit'),
    path('client/reservations/<int:pk>/delete/', views.reservation_delete, name='reservation_delete'),
    
    # Личный кабинет оператора
    path('operator/', views.operator_cabinet, name='operator_cabinet'),
    path('operator/reservations/', views.operator_reservations, name='operator_reservations'),
    path('operator/reservations/<int:pk>/', views.operator_reservation_detail, name='operator_reservation_detail'),
    path('operator/reservations/<int:pk>/delete/', views.operator_reservation_delete, name='operator_reservation_delete'),
    
    # Управление столиками (оператор)
    path('operator/tables/', views.operator_tables, name='operator_tables'),
    path('operator/tables/create/', views.operator_table_create, name='operator_table_create'),
    path('operator/tables/<int:pk>/', views.operator_table_detail, name='operator_table_detail'),
    path('operator/tables/<int:pk>/edit/', views.operator_table_edit, name='operator_table_edit'),
    path('operator/tables/<int:pk>/delete/', views.operator_table_delete, name='operator_table_delete'),
    
    # Управление блюдами (оператор)
    path('operator/dishes/', views.operator_dishes, name='operator_dishes'),
    path('operator/dishes/create/', views.operator_dish_create, name='operator_dish_create'),
    path('operator/dishes/<int:pk>/', views.operator_dish_detail, name='operator_dish_detail'),
    path('operator/dishes/<int:pk>/edit/', views.operator_dish_edit, name='operator_dish_edit'),
    path('operator/dishes/<int:pk>/delete/', views.operator_dish_delete, name='operator_dish_delete'),
    
    # Управление меню (оператор)
    path('operator/menus/', views.operator_menus, name='operator_menus'),
    path('operator/menus/view-date/', views.operator_menu_view_date, name='operator_menu_view_date'),
    path('operator/menus/create-all/', views.operator_menus_create_all, name='operator_menus_create_all'),
    path('operator/menus/<int:day_of_week>/edit/', views.operator_menu_edit, name='operator_menu_edit'),
    path('operator/menu-overrides/create/', views.operator_menu_override_create, name='operator_menu_override_create'),
    path('operator/menu-overrides/<int:pk>/', views.operator_menu_override_detail, name='operator_menu_override_detail'),
    path('operator/menu-overrides/<int:pk>/edit/', views.operator_menu_override_edit, name='operator_menu_override_edit'),
    path('operator/menu-overrides/<int:pk>/delete/', views.operator_menu_override_delete, name='operator_menu_override_delete'),

    path('operator/news/', views.operator_news_list, name='operator_news_list'),
    path('operator/news/create/', views.operator_news_create, name='operator_news_create'),
    path('operator/news/<int:pk>/edit/', views.operator_news_edit, name='operator_news_edit'),
    path('operator/news/<int:pk>/delete/', views.operator_news_delete, name='operator_news_delete'),
    path('operator/promotions/', views.operator_promotion_list, name='operator_promotion_list'),
    path('operator/promotions/create/', views.operator_promotion_create, name='operator_promotion_create'),
    path('operator/promotions/<int:pk>/edit/', views.operator_promotion_edit, name='operator_promotion_edit'),
    path('operator/promotions/<int:pk>/delete/', views.operator_promotion_delete, name='operator_promotion_delete'),
    path('operator/complaints/', views.operator_complaint_list, name='operator_complaint_list'),

    path('client/orders/', views.client_order_list, name='client_order_list'),
    path('client/orders/<int:pk>/', views.client_order_detail, name='client_order_detail'),
    path(
        'client/orders/<int:rid>/line/<int:line_id>/review/',
        views.client_dish_review_create,
        name='client_dish_review_create',
    ),
    path('client/complaints/', views.client_complaint_list, name='client_complaint_list'),
    path('client/complaints/new/', views.client_complaint_create, name='client_complaint_create'),
    path('client/news/', views.client_news_list, name='client_news_list'),
    path('client/news/<int:pk>/', views.client_news_detail, name='client_news_detail'),
    path('client/promotions/', views.client_promotion_list, name='client_promotion_list'),
]

