from django.http import Http404
from rest_framework.permissions import BasePermission


class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return False
        try:
            return user.profile.role == "client"
        except Exception:
            return False


class IsOwnerOr404(BasePermission):
    def has_object_permission(self, request, view, obj):
        owner_id = getattr(obj, "user_id", None)
        if owner_id == request.user.id:
            return True
        raise Http404
