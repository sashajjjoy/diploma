from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from bookings.models import Dish, News, Table, VenueComplaint
from bookings.services.availability import available_slots_for_date, occupied_slots_for_table_date
from bookings.services.menu import get_menu_dishes_for_date
from bookings.services.promotions import get_orderable_promotions
from bookings.services.reservations import cancel_reservation_for_client, reservation_detail_queryset

from .permissions import IsClientUser
from .serializers import (
    AuthTokenSerializer,
    ComplaintSerializer,
    CurrentUserSerializer,
    DishReviewCreateSerializer,
    DishReviewSerializer,
    DishSerializer,
    LogoutSerializer,
    MenuDaySerializer,
    NewsDetailSerializer,
    NewsListSerializer,
    PromotionDetailSerializer,
    PromotionListSerializer,
    ReservationCreateUpdateSerializer,
    ReservationDetailSerializer,
    ReservationListSerializer,
)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AuthTokenSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class RefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(CurrentUserSerializer(request.user).data)


class PublishedNewsListView(generics.ListAPIView):
    serializer_class = NewsListSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        now = timezone.now()
        return News.objects.filter(is_published=True, published_at__lte=now).order_by("-published_at")


class PublishedNewsDetailView(generics.RetrieveAPIView):
    serializer_class = NewsDetailSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        now = timezone.now()
        return News.objects.filter(is_published=True, published_at__lte=now).order_by("-published_at")


class PromotionListView(generics.ListAPIView):
    serializer_class = PromotionListSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return get_orderable_promotions()


class PromotionDetailView(generics.RetrieveAPIView):
    serializer_class = PromotionDetailSerializer
    permission_classes = [permissions.AllowAny]

    def get_object(self):
        promotions = {promotion.pk: promotion for promotion in get_orderable_promotions()}
        obj = promotions.get(int(self.kwargs["pk"]))
        if obj is None:
            raise Http404
        return obj


class MenuView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        date_raw = request.query_params.get("date")
        if not date_raw:
            raise serializers.ValidationError({"date": ["This query parameter is required."]})
        date_field = MenuDaySerializer().fields["date"]
        target_date = date_field.to_internal_value(date_raw)
        dish_ids = sorted(get_menu_dishes_for_date(target_date))
        dishes = Dish.objects.filter(pk__in=dish_ids).order_by("name")
        payload = {
            "date": target_date,
            "dish_ids": dish_ids,
            "dishes": DishSerializer(dishes, many=True, context={"request": request}).data,
        }
        return Response(MenuDaySerializer(payload, context={"request": request}).data)


class DishListView(generics.ListAPIView):
    serializer_class = DishSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = Dish.objects.filter(available_quantity__gt=0).order_by("name")
        date_raw = self.request.query_params.get("date")
        if date_raw:
            date_field = MenuDaySerializer().fields["date"]
            target_date = date_field.to_internal_value(date_raw)
            dish_ids = get_menu_dishes_for_date(target_date)
            queryset = queryset.filter(pk__in=dish_ids)
        return queryset


class OccupiedSlotsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        table_id = request.query_params.get("table_id")
        date_raw = request.query_params.get("date")
        if not table_id or not date_raw:
            raise serializers.ValidationError({"detail": ["table_id and date are required."]})
        table = get_object_or_404(Table, pk=table_id)
        date_field = MenuDaySerializer().fields["date"]
        target_date = date_field.to_internal_value(date_raw)
        reservation_id = request.query_params.get("reservation_id")
        occupied = occupied_slots_for_table_date(table, target_date, reservation_id=reservation_id)
        return Response(
            {
                "date": target_date.isoformat(),
                "occupied_slots": [
                    {
                        "start": item["start"],
                        "end": item["end"],
                        "start_datetime": item["start_datetime"].isoformat(),
                        "end_datetime": item["end_datetime"].isoformat(),
                    }
                    for item in occupied
                ],
            }
        )


class AvailableSlotsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        date_raw = request.query_params.get("date")
        guests_count = request.query_params.get("guests_count")
        if not date_raw or not guests_count:
            raise serializers.ValidationError({"detail": ["date and guests_count are required."]})
        date_field = MenuDaySerializer().fields["date"]
        target_date = date_field.to_internal_value(date_raw)
        guests_field = serializers.IntegerField(min_value=1)
        guests_count_value = guests_field.to_internal_value(guests_count)
        slots = available_slots_for_date(target_date, guests_count_value)
        return Response({"date": target_date.isoformat(), "available_slots": slots})


class ClientReservationListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_queryset(self):
        return reservation_detail_queryset().filter(user=self.request.user).order_by("-start_time")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ReservationCreateUpdateSerializer
        return ReservationListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()
        output = ReservationDetailSerializer(reservation, context={"request": request})
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)


class ClientReservationDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_queryset(self):
        return reservation_detail_queryset().filter(user=self.request.user).order_by("-start_time")

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return ReservationCreateUpdateSerializer
        return ReservationDetailSerializer

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()
        return Response(ReservationDetailSerializer(reservation, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        cancel_reservation_for_client(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientOrderListView(generics.ListAPIView):
    serializer_class = ReservationListSerializer
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_queryset(self):
        return reservation_detail_queryset().filter(user=self.request.user).order_by("-start_time")


class ClientOrderDetailView(generics.RetrieveAPIView):
    serializer_class = ReservationDetailSerializer
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_queryset(self):
        return reservation_detail_queryset().filter(user=self.request.user).order_by("-start_time")


class ComplaintListCreateView(generics.ListCreateAPIView):
    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_queryset(self):
        return VenueComplaint.objects.filter(user=self.request.user).order_by("-created_at")


class DishReviewCreateView(generics.CreateAPIView):
    serializer_class = DishReviewCreateSerializer
    permission_classes = [permissions.IsAuthenticated, IsClientUser]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["reservation"] = get_object_or_404(
            reservation_detail_queryset().filter(user=self.request.user),
            pk=self.kwargs["reservation_id"],
        )
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = serializer.save()
        return Response(
            DishReviewSerializer(review, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
