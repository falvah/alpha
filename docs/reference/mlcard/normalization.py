"""Нормализация входных данных.

Бэкенд присылает "да/нет/пусто" и числа в свободном виде, поэтому все
преобразования собраны здесь. Ошибки контракта поднимаются как
``InputDataError`` и не должны молча превращаться в "нет данных".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class InputDataError(ValueError):
    """Входные данные не соответствуют контракту."""


_TRUE_TOKENS = {"да", "yes", "true", "1", "y", "есть", "истина"}
_FALSE_TOKENS = {"нет", "no", "false", "0", "n", "отсутствует", "ложь"}
_EMPTY_TOKENS = {
    "",
    "пусто",
    "нет данных",
    "неизвестно",
    "unknown",
    "none",
    "null",
    "-",
    "—",
    "–",
    "н/д",
    "н.д.",
    "nd",
    "n/a",
}


_EMPTY_NORMALIZED = _EMPTY_TOKENS


def is_blank(value: Any) -> bool:
    """Пустое ли значение (None, пустая строка, маркеры "пусто")."""
    if value is None:
        return True
    if isinstance(value, str):
        return normalize_text(value) in _EMPTY_NORMALIZED
    return False


def parse_tristate(value: Any, *, field: str = "value") -> bool | None:
    """Разбирает "да/нет/пусто" в ``True/False/None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        raise InputDataError(f"{field}: ожидалось да/нет/пусто, получено {value!r}")
    if not isinstance(value, str):
        raise InputDataError(f"{field}: ожидалось да/нет/пусто, получено {value!r}")

    token = normalize_text(value)
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    if token in _EMPTY_TOKENS:
        return None
    raise InputDataError(f"{field}: неизвестное значение {value!r} (ожидалось да/нет/пусто)")


def parse_decimal(value: Any, *, field: str = "value") -> Decimal | None:
    """Разбирает сумму: "1 500 000,50", 1500000, "1500000 руб."."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise InputDataError(f"{field}: ожидалось число, получено {value!r}")
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if normalize_text(text) in _EMPTY_TOKENS:
        return None

    text = re.sub(r"(?i)(руб\.?|₽|rub)", "", text)
    text = (
        text.replace("\u00a0", "")
        .replace("\u202f", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise InputDataError(f"{field}: не удалось разобрать число {value!r}") from exc
    if not number.is_finite():
        raise InputDataError(f"{field}: число должно быть конечным, получено {value!r}")
    return number


def parse_int(value: Any, *, field: str = "value") -> int | None:
    """Разбирает целое число (месяцы, количество)."""
    number = parse_decimal(value, field=field)
    if number is None:
        return None
    if number != number.to_integral_value():
        raise InputDataError(f"{field}: ожидалось целое число, получено {value!r}")
    return int(number)


def parse_count(value: Any, *, field: str = "value") -> int | None:
    """Неотрицательное целое (количество кредитов, владельцев и т.п.)."""
    number = parse_int(value, field=field)
    if number is not None and number < 0:
        raise InputDataError(f"{field}: отрицательное значение {number}")
    return number


def parse_date(value: Any, *, field: str = "value") -> date | None:
    """Разбирает дату: ISO, ДД.ММ.ГГГГ или ISO-datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if normalize_text(text) in _EMPTY_TOKENS:
        return None

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise InputDataError(f"{field}: не удалось разобрать дату {value!r}")


def months_between(start: date, end: date) -> int:
    """Число полных календарных месяцев между датами (не отрицательное)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def normalize_text(value: Any) -> str:
    """Приводит текст к сравнимому виду: нижний регистр, ё→е, без лишних пробелов."""
    if value is None:
        return ""
    text = str(value).strip().lower().replace("ё", "е")
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text)


def normalize_legal_form(value: Any) -> str | None:
    """Каноническая организационно-правовая форма: ооо / ао / ип."""
    if is_blank(value):
        return None
    text = normalize_text(value)
    if not text:
        return None
    if text.startswith("ооо") or "ограниченной ответственностью" in text:
        return "ооо"
    if "акционерное общество" in text or text in {"ао", "пао", "зао", "оао", "нао"}:
        return "ао"
    if text in {"ип", "индивидуальный предприниматель"}:
        return "ип"
    return text


def classify_purpose(value: Any) -> str | None:
    """Относит свободный текст цели кредита к категории карты."""
    if is_blank(value):
        return None
    text = normalize_text(value)
    if not text:
        return None
    if any(k in text for k in ("рефинанс", "перекредит", "погашени кредит", "закрыти кредит")):
        return "refinancing"
    if any(
        k in text
        for k in ("оборудован", "техник", "станок", "транспорт", "автомобил", "авто", "машин")
    ):
        return "equipment"
    if any(
        k in text
        for k in ("строительств", "недвижим", "инвестиц", "модернизац", "ремонт", "расширен")
    ):
        return "investment"
    if any(k in text for k in ("оборотн", "пополнени", "сырь", "запас", "закуп", "товар")):
        return "working_capital"
    return "other"


def normalize_collateral_type(value: Any) -> str | None:
    """Канонический тип залога: недвижимость / транспорт / оборудование / прочее."""
    if is_blank(value):
        return None
    text = normalize_text(value)
    if not text:
        return None
    if any(k in text for k in ("недвиж", "здани", "помещени", "земл")):
        return "недвижимость"
    if any(k in text for k in ("транспорт", "авто", "машин", "спецтехник")):
        return "транспорт"
    if any(k in text for k in ("оборудован", "станок", "техник")):
        return "оборудование"
    return "прочее"


def parse_okved_section(value: Any) -> int | None:
    """Возвращает первые две цифры ОКВЭД (раздел/класс) или None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(\d{1,2})", text)
    if not match:
        return None
    section = int(match.group(1))
    return section if 1 <= section <= 99 else None
