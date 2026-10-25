"""One JSON boundary for forms and the scoring API."""
import json
from .normalization import InputDataError

MAX_PAYLOAD_BYTES = 65536


def _reject_constant(value):
    raise InputDataError("Неконечное число в JSON.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputDataError(f"Поле {key} передано дважды.")
        result[key] = value
    return result


def parse_payload(value):
    try:
        raw = value.encode("utf-8") if isinstance(value, str) else value
        if not isinstance(raw, bytes) or len(raw) > MAX_PAYLOAD_BYTES:
            raise InputDataError("Максимальный размер JSON — 64 КБ.")
        result = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputDataError("Некорректный JSON: " + str(exc)[:200]) from exc
    validate_structure(result)
    return result


def validate_structure(value):
    pending = [(value, 0)]
    visited = 0
    while pending:
        item, depth = pending.pop()
        visited += 1
        if depth > 8 or visited > 1000:
            raise InputDataError("JSON слишком сложный: максимум 8 уровней и 1000 значений.")
        if isinstance(item, dict):
            pending.extend((value, depth + 1) for value in item.values())
        elif isinstance(item, list):
            pending.extend((value, depth + 1) for value in item)
