from django import template

register = template.Library()


@register.filter
def lookup(mapping, key):
    """Доступ к dict в шаблоне: {{ mydict|lookup:dish.pk }}"""
    if not mapping:
        return []
    if key is None:
        return []
    v = mapping.get(key)
    if v is None and key is not None:
        try:
            v = mapping.get(int(key))
        except (TypeError, ValueError):
            pass
    return v if v is not None else []
