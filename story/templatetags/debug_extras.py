import json
from django import template

register = template.Library()

@register.filter
def prettyjson(value):
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        return str(value)
