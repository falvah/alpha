"""Strict parsing for the bilingual scorecard contract; no Django dependency."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class InputDataError(ValueError):
    pass


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value).lower().replace("ё", "е").replace("_", " ")).strip()


def missing(value):
    return value is None or isinstance(value, str) and normalize_text(value) in {"", "пусто", "null", "none"}


def parse_tristate(value):
    if missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = normalize_text(value)
        if text in {"да", "true", "1"}:
            return True
        if text in {"нет", "false", "0"}:
            return False
    raise InputDataError(f"Ожидалось да / нет / пусто: {value!r}.")


def parse_decimal(value):
    if missing(value):
        return None
    if isinstance(value, bool):
        raise InputDataError("Логическое значение не является суммой.")
    text = re.sub(r"\s*(?:руб\.?|₽)$", "", str(value).lower().strip())
    text = re.sub(r"\s", "", text).replace(",", ".")
    if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?", text):
        raise InputDataError("Некорректное число.")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise InputDataError("Некорректное число.") from exc
    if not result.is_finite() or abs(result) > Decimal("1e14") or result.as_tuple().exponent < -12:
        raise InputDataError("Число должно быть конечным, не более 10¹⁴ и 12 знаков после запятой.")
    return result


def parse_money(value):
    result = parse_decimal(value)
    if result is not None and result != result.quantize(Decimal("0.01")):
        raise InputDataError("Денежная сумма должна содержать не более двух знаков после запятой.")
    return result


def parse_int(value):
    result = parse_decimal(value)
    if result is not None and result != result.to_integral_value():
        raise InputDataError("Ожидалось целое число.")
    return int(result) if result is not None else None


def parse_count(value):
    result = parse_int(value)
    if result is not None and result < 0:
        raise InputDataError("Количество не может быть отрицательным.")
    return result


def parse_date(value):
    if missing(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise InputDataError("Некорректная дата.") from exc


def months_between(start, end):
    if start > end:
        raise InputDataError("Дата регистрации позже даты оценки.")
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def normalize_legal_form(value):
    if value is not None and not isinstance(value, str):
        raise InputDataError("Форма организации должна быть строкой.")
    text = normalize_text(value or "")
    result = {"общество с ограниченной ответственностью": "ооо", "акционерное общество": "ао", "индивидуальный предприниматель": "ип"}.get(text, text)
    if result and result not in {"ооо", "ао", "ип", "кфх"}:
        raise InputDataError("Неизвестная форма организации.")
    return result


def normalize_collateral_type(value):
    if value is not None and not isinstance(value, str):
        raise InputDataError("Тип залога должен быть строкой.")
    text = normalize_text(value or "")
    for canonical, words in {"недвижимость": ("недвиж", "real estate", "здани", "помещен"), "транспорт": ("транспорт", "автомоб", "vehicle"), "оборудование": ("оборуд", "equipment")}.items():
        if any(word in text for word in words):
            return canonical
    return "прочее" if text else None


def classify_purpose(value):
    text = normalize_text(value or "")
    for category, words in {"refinancing": ("рефинанс", "refinanc"), "equipment": ("оборуд", "equipment"), "working_capital": ("оборот", "working capital", "товар"), "investment": ("инвест", "investment", "развит")}.items():
        if any(word in text for word in words):
            return category
    return "other"


def parse_okved_section(value):
    if missing(value):
        return None
    match = re.match(r"^(\d{2})(?:\.|$)", str(value).strip())
    if not match:
        raise InputDataError("ОКВЭД должен начинаться с двух цифр.")
    return match[1]
