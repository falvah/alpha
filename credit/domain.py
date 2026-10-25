"""Loan calculations and a deterministic cash-flow assessment policy."""
from decimal import Decimal, ROUND_HALF_UP
import re

PRODUCTS = {
    "working": {"name": "Оборотный кредит", "tag": "На каждый день", "icon": "arrows", "rate": "24.0", "min": 100000, "max": 10000000, "months": 36, "description": "Закупайте товары, платите поставщикам и поддерживайте оборот бизнеса.", "purpose": "Пополнение оборотных средств"},
    "growth": {"name": "На развитие бизнеса", "tag": "Для больших планов", "icon": "growth", "rate": "22.5", "min": 500000, "max": 30000000, "months": 60, "description": "Откройте новую точку, обновите оборудование или запустите направление.", "purpose": "Покупка оборудования и развитие бизнеса"},
    "bridge": {"name": "На кассовый разрыв", "tag": "Когда нужно вовремя", "icon": "bolt", "rate": "25.0", "min": 100000, "max": 5000000, "months": 12, "description": "Покройте временные расходы, пока ждёте оплату от покупателей.", "purpose": "Покрытие временного кассового разрыва"},
}
STATUSES = {
    "draft": "Черновик", "submitted": "На проверке", "review": "На рассмотрении",
    "info": "Нужны уточнения", "approved": "Одобрено", "counteroffer": "Новые условия",
    "rejected": "Не одобрено", "funded": "Средства выданы", "cancelled": "Отменена",
}
TRANSITIONS = {
    "draft": {"submitted", "cancelled"}, "submitted": {"review", "approved", "rejected", "cancelled"},
    "review": {"info", "approved", "counteroffer", "rejected", "cancelled"},
    "info": {"submitted", "cancelled"}, "approved": {"funded", "cancelled"},
    "counteroffer": {"funded", "cancelled"}, "rejected": set(), "funded": set(), "cancelled": set(),
}

def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def payment(amount, months, rate):
    principal, rate = Decimal(str(amount)), Decimal(str(rate))
    if not principal.is_finite() or not rate.is_finite() or principal <= 0 or not 1 <= months <= 120 or not 0 <= rate <= 100:
        raise ValueError("Некорректные параметры расчёта.")
    r = rate / 1200
    return money(principal / months if not r else principal * r / (1 - (1 + r) ** -months))

def schedule(amount, months, rate):
    balance = money(amount)
    monthly = payment(amount, months, rate)
    rows = []
    for index in range(1, months + 1):
        interest = money(balance * Decimal(str(rate)) / 1200)
        principal = min(balance, monthly - interest) if index < months else balance
        balance = money(balance - principal)
        rows.append({"month": index, "payment": money(principal + interest), "principal": principal, "interest": interest, "balance": balance})
    return rows

def validate_identity(kind, inn, ogrn):
    lengths = (12, 15) if kind == "ИП" else (10, 13)
    return bool(kind in {"ООО", "АО", "ИП", "КФХ"}
                and re.fullmatch(r"[0-9]{%d}" % lengths[0], inn)
                and re.fullmatch(r"[0-9]{%d}" % lengths[1], ogrn)
                and len(set(inn)) > 1 and len(set(ogrn)) > 1)

def ensure_transition(current, target):
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError("Статус заявки уже изменился. Обновите страницу.")
