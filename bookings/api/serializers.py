from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from bookings.models import (
    Booking,
    CustomerOrder,
    Dish,
    News,
    OrderAppliedPromotion,
    OrderItem,
    OrderItemReview,
    Promotion,
    PromotionComboItem,
    ServiceWeekdayWindow,
    VenueComplaint,
)
from bookings.services.availability import get_duration_values
from bookings.services.promotions import available_quantity_net
from bookings.services.reservations import (
    create_dish_review,
    create_or_update_reservation_for_client,
    get_public_id,
)
from bookings.services.security import clear_login_attempt, is_login_locked, record_failed_login


class AuthTokenSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        locked, attempt = is_login_locked(attrs["username"].strip())
        if locked and attempt is not None:
            raise serializers.ValidationError({"detail": ["Account is temporarily locked."]})
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            record_failed_login(attrs["username"].strip(), self.context.get("request"))
            raise serializers.ValidationError({"detail": ["Invalid username or password."]})
        clear_login_attempt(attrs["username"].strip())
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


class OrderItemSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    dish_id = serializers.IntegerField(read_only=True)
    dish_name = serializers.CharField(source="dish_name_snapshot", read_only=True)
    unit_price = serializers.DecimalField(source="unit_price_snapshot", max_digits=10, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(source="line_total_snapshot", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ("id", "dish_id", "dish_name", "quantity", "unit_price", "line_total")

    def get_id(self, obj):
        return obj.public_id or obj.pk


class AppliedPromotionSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="promotion.id")
    name = serializers.CharField(source="promotion.name")
    discount_amount = serializers.DecimalField(source="discount_amount_snapshot", max_digits=10, decimal_places=2)

    class Meta:
        model = OrderAppliedPromotion
        fields = ("id", "name", "discount_amount")


class ReservationListSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    table_id = serializers.IntegerField(source="table.id", read_only=True)
    order_total = serializers.DecimalField(source="order.total_amount", max_digits=10, decimal_places=2, read_only=True)
    dishes = OrderItemSerializer(source="order.items", many=True, read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "table_id",
            "guests_count",
            "start_time",
            "end_time",
            "order_total",
            "dishes",
        )

    def get_id(self, obj):
        return get_public_id(obj)


class ReservationDetailSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    table_id = serializers.IntegerField(source="table.id", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    order_subtotal = serializers.DecimalField(source="order.subtotal_amount", max_digits=10, decimal_places=2, read_only=True)
    promotion_discount_total = serializers.DecimalField(source="order.discount_total", max_digits=10, decimal_places=2, read_only=True)
    order_total = serializers.DecimalField(source="order.total_amount", max_digits=10, decimal_places=2, read_only=True)
    applied_promotions = AppliedPromotionSerializer(source="order.applied_promotions", many=True, read_only=True)
    dishes = OrderItemSerializer(source="order.items", many=True, read_only=True)

    class Meta:
        model = Booking
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

    def get_id(self, obj):
        return get_public_id(obj)


class OrderListSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    table_id = serializers.IntegerField(source="booking.table.id", read_only=True)
    guests_count = serializers.IntegerField(source="booking.guests_count", read_only=True)
    start_time = serializers.DateTimeField(source="booking.start_time", read_only=True)
    end_time = serializers.DateTimeField(source="booking.end_time", read_only=True)
    order_total = serializers.DecimalField(source="total_amount", max_digits=10, decimal_places=2, read_only=True)
    dishes = OrderItemSerializer(source="items", many=True, read_only=True)

    class Meta:
        model = CustomerOrder
        fields = (
            "id",
            "table_id",
            "guests_count",
            "start_time",
            "end_time",
            "order_total",
            "dishes",
        )

    def get_id(self, obj):
        return get_public_id(obj)


class OrderDetailSerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    table_id = serializers.IntegerField(source="booking.table.id", read_only=True)
    guests_count = serializers.IntegerField(source="booking.guests_count", read_only=True)
    start_time = serializers.DateTimeField(source="booking.start_time", read_only=True)
    end_time = serializers.DateTimeField(source="booking.end_time", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    order_subtotal = serializers.DecimalField(source="subtotal_amount", max_digits=10, decimal_places=2, read_only=True)
    promotion_discount_total = serializers.DecimalField(source="discount_total", max_digits=10, decimal_places=2, read_only=True)
    order_total = serializers.DecimalField(source="total_amount", max_digits=10, decimal_places=2, read_only=True)
    applied_promotions = AppliedPromotionSerializer(many=True, read_only=True)
    dishes = OrderItemSerializer(source="items", many=True, read_only=True)

    class Meta:
        model = CustomerOrder
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

    def get_id(self, obj):
        return get_public_id(obj)


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

    def _legacy_instance(self):
        instance = getattr(self, "instance", None)
        if instance is None:
            return None
        return instance

    def validate(self, attrs):
        instance = self._legacy_instance()
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
            allowed_durations = get_duration_values()
            if attrs.get("duration_minutes") not in allowed_durations:
                raise serializers.ValidationError(
                    {"duration_minutes": [f"Supported values are: {', '.join(str(value) for value in allowed_durations)} minutes."]}
                )
            if attrs.get("guests_count") is None:
                raise serializers.ValidationError({"guests_count": ["This field is required for table reservations."]})
        else:
            attrs["guests_count"] = 1

        if not ServiceWeekdayWindow.is_service_day(attrs["date"]):
            raise serializers.ValidationError({"date": ["Reservations are available only on active service days."]})
        return attrs

    def _service_payload(self):
        attrs = self.validated_data
        instance = self._legacy_instance()
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
        fields = (
            "id",
            "subject",
            "message",
            "status",
            "created_at",
            "related_booking_id",
            "related_order_id",
        )
        read_only_fields = ("id", "status", "created_at")

    def create(self, validated_data):
        return VenueComplaint.objects.create(user=self.context["request"].user, **validated_data)


class DishReviewCreateSerializer(serializers.Serializer):
    reservation_dish = serializers.IntegerField()
    dish = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        order = self.context["order"]
        try:
            order_item = order.items.select_related("dish").get(
                Q(public_id=attrs["reservation_dish"]) | Q(pk=attrs["reservation_dish"])
            )
        except OrderItem.DoesNotExist:
            raise serializers.ValidationError({"reservation_dish": ["Order line not found."]})
        if order_item.dish_id != attrs["dish"]:
            raise serializers.ValidationError({"dish": ["Dish does not match the order line."]})
        attrs["order_item_obj"] = order_item
        return attrs

    def create(self, validated_data):
        try:
            return create_dish_review(
                user=self.context["request"].user,
                order=self.context["order"],
                order_item=validated_data["order_item_obj"],
                rating=validated_data["rating"],
                comment=validated_data.get("comment", ""),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(getattr(exc, "message_dict", {"detail": exc.messages}))


class DishReviewSerializer(serializers.ModelSerializer):
    reservation_dish_id = serializers.SerializerMethodField()
    dish_id = serializers.IntegerField(source="order_item.dish_id", read_only=True)

    class Meta:
        model = OrderItemReview
        fields = ("id", "reservation_dish_id", "dish_id", "rating", "comment", "created_at")

    def get_reservation_dish_id(self, obj):
        return obj.order_item.public_id or obj.order_item_id
