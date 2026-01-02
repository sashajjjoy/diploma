from django.urls import path
from . import views

urlpatterns = [
    # Общие
    path('', views.dashboard, name='dashboard'),
    
    # API для получения занятых временных слотов
    path('api/occupied-slots/', views.get_occupied_time_slots, name='get_occupied_time_slots'),
    
    # Бронирования клиента
    path('reservations/create/', views.reservation_create, name='reservation_create'),
    path('client/reservations/<int:pk>/', views.reservation_detail, name='reservation_detail'),
    path('client/reservations/<int:pk>/edit/', views.reservation_edit, name='reservation_edit'),
    path('client/reservations/<int:pk>/delete/', views.reservation_delete, name='reservation_delete'),
    path('client/reservations/<int:reservation_pk>/dishes/add/', views.reservation_dish_add, name='reservation_dish_add'),
    path('client/reservations/<int:reservation_pk>/dishes/<int:dish_pk>/delete/', views.reservation_dish_delete, name='reservation_dish_delete'),
    
    # Личный кабинет оператора
    path('operator/', views.operator_cabinet, name='operator_cabinet'),
    path('operator/reservations/', views.operator_reservations, name='operator_reservations'),
    path('operator/reservations/<int:pk>/', views.operator_reservation_detail, name='operator_reservation_detail'),
    
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
]

