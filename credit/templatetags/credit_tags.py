from decimal import Decimal, InvalidOperation
from django import template
register = template.Library()

@register.filter
def percent(value):
    try:
        return f"{Decimal(str(value)) * 100:.2f}%".replace(".", ",")
    except (InvalidOperation, TypeError, ValueError):
        return "—"

@register.filter
def rub(value):
    try:
        return f"{Decimal(value):,.0f}".replace(",", " ")
    except (InvalidOperation, TypeError, ValueError):
        return "—"

@register.filter
def compact(value):
    try:
        amount = Decimal(value)
        return f"{amount / 1000000:.1f}".replace(".", ",") + " млн" if amount >= 1000000 else rub(amount)
    except (InvalidOperation, TypeError):
        return "—"
