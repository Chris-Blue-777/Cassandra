import json
from django import template
from django.utils.safestring import mark_safe
from django.core.serializers.json import DjangoJSONEncoder

register = template.Library()

@register.filter
def prettyjson(value):
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        return str(value)

@register.filter
def to_json(value):
    try:
        return mark_safe(json.dumps(value, cls=DjangoJSONEncoder))
    except TypeError:
        return mark_safe(json.dumps(str(value)))
