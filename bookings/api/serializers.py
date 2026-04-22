from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from bookings.models import (
    Dish,
    DishReview,
    News,
    Promotion,
    PromotionComboItem,
    Reservation,
    ReservationDish,
    VenueComplaint,
)
from bookings.services.menu import get_menu_dishes_for_date
from bookings.services.promotions import available_quantity_net, get_orderable_promotions
from bookings.services.reservations import create_dish_review, create_or_update_reservation_for_client


class AuthTokenSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError({"detail": ["Invalid username or password."]})
        refresh = RefreshToken.for_user(user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def save(self, **kwargs):
        token = RefreshToken(self.validated_data["refresh"])
        token.blacklist()


class CurrentUserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "role")

    def get_role(self, obj):
        if obj.is_superuser:
            return "admin"
        try:
            return obj.profile.role
        except Exception:
            return "client"


class NewsListSerializer(serializers.ModelSerializer):
    class Meta:
        model = News
        fields = ("id", "title", "summary", "published_at")


class NewsDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = News
        fields = ("id", "title", "summary", "body", "published_at")


class PromotionComboItemSerializer(serializers.ModelSerializer):
    dish_id = serializers.IntegerField(source="dish.id", read_only=True)
    dish_name = serializers.CharField(source="dish.name", read_only=True)

    class Meta:
        model = PromotionComboItem
        fields = ("dish_id", "dish_name", "min_quantity")


class PromotionListSerializer(serializers.ModelSerializer):
    combo_items = PromotionComboItemSerializer(many=True, read_only=True)
    target_dish_id = serializers.IntegerField(source="target_dish.id", read_only=True)
    target_dish_name = serializers.CharField(source="target_dish.name", read_only=True)

    class Meta:
        model = Promotion
        fields = (
            "id",
            "name",
            "description",
            "kind",
            "discount_type",
            "discount_value",
            "valid_from",
            "valid_to",
            "target_dish_id",
            "target_dish_name",
            "combo_items",
        )


class PromotionDetailSerializer(PromotionListSerializer):
    pass


class DishSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()

    class Meta:
        model = Dish
        fields = (
            "id",
            "name",
            "description",
            "price",
            "available_quantity",
            "image_url",
        )

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url

    def get_available_quantity(self, obj):
        return available_quantity_net(obj)


class MenuDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    dish_ids = serializers.ListField(child=serializers.IntegerField())
    dishes = serializers.ListField(child=serializers.DictField())


class ReservationDishSerializer(serializers.ModelSerializer):
    dish_id = serializers.IntegerField(source="dish.id", read_only=True)
    dish_name = serializers.CharField(source="dish.name", read_only=True)
    unit_price = serializers.DecimalField(source="dish.price", max_digits=10, decimal_places=2, read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = ReservationDish
        fields = ("id", "dish_id", "dish_name", "quantity", "unit_price", "line_total")

    def get_line_total(self, obj):
        return obj.dish.price * obj.quantity


class AppliedPromotionSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="promotion.id")
    name = serializers.CharField(source="promotion.name")
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class ReservationListSerializer(serializers.ModelSerializer):
    dishes = ReservationDishSerializer(many=True, read_only=True)

    class Meta:
        model = Reservation
        fields = (
            "id",
            "table_id",
            "guests_count",
            "start_time",
            "end_time",
            "order_total",
            "dishes",
        )


class ReservationDetailSerializer(serializers.ModelSerializer):
    dishes = ReservationDishSerializer(many=True, read_only=True)
    applied_promotions = AppliedPromotionSerializer(
        source="applied_promotion_links", many=True, read_only=True
    )

    class Meta:
        model = Reservation
        fields = (
            "id",
            "table_id",
            "guests_count",
            "start_time",
            "end_time",
            "created_at",
            "order_subtotal",
            "promotion_discount_total",
            "order_total",
            "applied_promotions",
            "dishes",
        )


class ReservationDishInputSerializer(serializers.Serializer):
    dish = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class ReservationCreateUpdateSerializer(serializers.Serializer):
    takeout = serializers.BooleanField(required=False, default=False)
    date = serializers.DateField()
    time = serializers.TimeField(required=False)
    duration_minutes = serializers.IntegerField(required=False)
    guests_count = serializers.IntegerField(required=False, min_value=1)
    promotion_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    dishes = ReservationDishInputSerializer(many=True, required=False)

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        existing_takeout = bool(instance and instance.table_id is None)
        takeout = attrs.get("takeout", existing_takeout)
        attrs["takeout"] = takeout
        if instance is not None:
            attrs.setdefault("date", timezone.localtime(instance.start_time).date())
        if not takeout:
            if attrs.get("time") is None and instance is not None:
                attrs["time"] = timezone.localtime(instance.start_time).time().replace(second=0, microsecond=0)
            if attrs.get("duration_minutes") is None and instance is not None:
                attrs["duration_minutes"] = int((instance.end_time - instance.start_time).total_seconds() / 60)
            if attrs.get("guests_count") is None and instance is not None:
                attrs["guests_count"] = instance.guests_count
            if attrs.get("time") is None:
                raise serializers.ValidationError({"time": ["This field is required for table reservations."]})
            if attrs.get("duration_minutes") is None:
                raise serializers.ValidationError({"duration_minutes": ["This field is required for table reservations."]})
            if attrs.get("duration_minutes") not in (25, 55):
                raise serializers.ValidationError({"duration_minutes": ["Supported values are 25 or 55 minutes."]})
            if attrs.get("guests_count") is None:
                raise serializers.ValidationError({"guests_count": ["This field is required for table reservations."]})
        else:
            attrs["guests_count"] = 1

        if attrs["date"].weekday() >= 5:
            raise serializers.ValidationError({"date": ["Reservations are available only on working days."]})
        return attrs

    def _service_payload(self):
        attrs = self.validated_data
        instance = getattr(self, "instance", None)
        payload = {
            "takeout": attrs.get("takeout", False),
            "date": attrs["date"],
            "promotion_ids": attrs.get("promotion_ids") if "promotion_ids" in attrs else (
                list(instance.applied_promotion_links.values_list("promotion_id", flat=True))
                if instance is not None
                else []
            ),
            "dishes": attrs.get("dishes") if "dishes" in attrs else (
                [{"dish": line.dish_id, "quantity": line.quantity} for line in instance.dishes.all()]
                if instance is not None
                else []
            ),
        }
        if attrs.get("time") is not None:
            payload["time"] = attrs["time"].strftime("%H:%M")
        if attrs.get("duration_minutes") is not None:
            payload["duration_minutes"] = attrs["duration_minutes"]
        if attrs.get("guests_count") is not None:
            payload["guests_count"] = attrs["guests_count"]
        return payload

    def create(self, validated_data):
        try:
            return create_or_update_reservation_for_client(
                user=self.context["request"].user,
                data=self._service_payload(),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", {"detail": exc.messages}))

    def update(self, instance, validated_data):
        try:
            return create_or_update_reservation_for_client(
                user=self.context["request"].user,
                data=self._service_payload(),
                instance=instance,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", {"detail": exc.messages}))


class ComplaintSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueComplaint
        fields = ("id", "subject", "message", "status", "created_at")
        read_only_fields = ("id", "status", "created_at")

    def create(self, validated_data):
        return VenueComplaint.objects.create(user=self.context["request"].user, **validated_data)


class DishReviewCreateSerializer(serializers.Serializer):
    reservation_dish = serializers.IntegerField()
    dish = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        reservation = self.context["reservation"]
        try:
            reservation_dish = reservation.dishes.select_related("dish").get(pk=attrs["reservation_dish"])
        except ReservationDish.DoesNotExist:
            raise serializers.ValidationError({"reservation_dish": ["Order line not found."]})
        if reservation_dish.dish_id != attrs["dish"]:
            raise serializers.ValidationError({"dish": ["Dish does not match the order line."]})
        attrs["reservation_dish_obj"] = reservation_dish
        return attrs

    def create(self, validated_data):
        try:
            return create_dish_review(
                user=self.context["request"].user,
                reservation=self.context["reservation"],
                reservation_dish=validated_data["reservation_dish_obj"],
                rating=validated_data["rating"],
                comment=validated_data.get("comment", ""),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", {"detail": exc.messages}))


class DishReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = DishReview
        fields = ("id", "reservation_dish_id", "dish_id", "rating", "comment", "created_at")
