from django import template

register = template.Library()


@register.filter
def get_user_role(user):
    """Безопасное получение роли пользователя"""
    if not user or not user.is_authenticated:
        return None
    try:
        return user.profile.role
    except:
        return None






