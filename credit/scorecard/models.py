from dataclasses import dataclass
from decimal import Decimal
from .normalization import (
    InputDataError, missing, parse_count, parse_decimal, parse_money, parse_date, parse_tristate,
    months_between, normalize_legal_form, normalize_collateral_type, classify_purpose, parse_okved_section,
)


def _pick(data, *keys):
    if sum(key in data for key in keys) > 1:
        raise InputDataError(f"Передайте поле {keys[0]} только один раз: русский либо английский ключ.")
    for key in keys:
        if key in data:
            return data[key]
    return None


def block(data, *keys):
    value = _pick(data, *keys)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InputDataError(f"Блок {keys[0]} должен быть объектом.")
    return value


@dataclass(frozen=True)
class LoanRequest:
    amount: Decimal
    term_months: int
    application: dict
    features: dict
    branch: str | None
    valuation_date: object
    card_version: str | None

    @classmethod
    def from_payload(cls, payload):
        from .payload import validate_structure
        validate_structure(payload)
        if not isinstance(payload, dict):
            raise InputDataError("Заявка должна быть JSON-объектом.")
        app = block(payload, "заявка", "application")
        enrichment = block(payload, "обогащение", "enrichment")
        document = block(payload, "документ", "document")
        service = block(payload, "служебное", "service")
        history = block(enrichment, "кредитная_история", "credit_history")
        ownership = block(enrichment, "собственность", "ownership")
        related = block(enrichment, "связанные_стороны", "related_parties")
        amount = parse_money(_pick(app, "сумма", "amount"))
        term = parse_count(_pick(app, "срок", "term", "term_months"))
        valuation = parse_date(_pick(service, "дата_оценки", "valuation_date"))
        if amount is None or amount <= 0 or term is None or not 1 <= term <= 120 or valuation is None:
            raise InputDataError("Обязательны положительная сумма, срок от 1 до 120 месяцев и дата оценки.")
        registration = parse_date(_pick(enrichment, "дата_регистрации", "registration_date"))
        f = {"months_since_registration": months_between(registration, valuation) if registration else None,
             "okved_section": parse_okved_section(_pick(enrichment, "оквэд", "okved"))}
        for key, aliases in {
            "address_mass": ("адрес_массовый",), "address_unreliable": ("адрес_недостоверный",),
            "director_disqualified": ("руководитель_дисквалиф.", "руководитель_дисквалифицирован"),
            "director_mass": ("руководитель_массовый",),
        }.items():
            f[key] = parse_tristate(_pick(enrichment, *aliases, key))
        f["nominal_owner"] = parse_tristate(_pick(ownership, "номинальный_владелец", "nominal_owner"))
        f["has_history"] = parse_tristate(_pick(history, "есть_история", "has_history"))
        for key, source, alias in [
            ("companies_per_director", enrichment, "юрлиц_у_руководителя"),
            ("max_overdue_12m", history, "макс_просрочка_12м"), ("active_loans", history, "активных_кредитов"),
            ("owners_count", ownership, "число_владельцев"),
        ]:
            f[key] = parse_count(_pick(source, alias, key))
        related_value = _pick(enrichment, "число_связанных", "related_parties_count")
        f["related_parties_count"] = parse_count(related_value if related_value is not None else _pick(related, "число_связанных", "related_parties_count"))
        mature = {"months_with_inflows": ("месяцев_с_поступлениями",), "avg_monthly_inflow": ("средний_приход",), "variability": ("вариативность",)}
        young = {"has_guarantee": ("есть_поручительство",), "guarantor_income": ("доход_поручителя",), "collateral_value": ("стоимость_залога",), "collateral_type": ("тип_залога",)}
        branches = []
        for branch, fields in [("mature", mature), ("young", young)]:
            if any(not missing(_pick(document, *aliases, key)) for key, aliases in fields.items()):
                branches.append(branch)
            for key, aliases in fields.items():
                parser = parse_count if key == "months_with_inflows" else parse_tristate if key == "has_guarantee" else normalize_collateral_type if key == "collateral_type" else parse_decimal
                if key in {"avg_monthly_inflow", "guarantor_income", "collateral_value"}:
                    parser = parse_money
                value = _pick(document, *aliases, key)
                f[key] = None if missing(value) else parser(value)
                if isinstance(f[key], Decimal) and f[key] < 0:
                    raise InputDataError(f"{key}: отрицательное значение недопустимо.")
        if len(branches) > 1:
            raise InputDataError("Нельзя одновременно заполнять mature и young в документе.")
        if f["months_with_inflows"] is not None and f["months_since_registration"] is not None and f["months_with_inflows"] > f["months_since_registration"]:
            raise InputDataError("Период поступлений не может превышать возраст компании.")
        canonical = {key: _pick(app, alias, key) for key, alias in {"name": "название", "signatory": "в_лице", "address": "адрес", "inn": "инн", "ogrn": "огрн", "purpose": "цель", "document_url": "ссылка_на_документ"}.items()}
        if any(value is not None and not isinstance(value, str) for value in canonical.values()):
            raise InputDataError("Реквизиты, цель и ссылка должны быть строками.")
        canonical["legal_form"] = normalize_legal_form(_pick(app, "форма", "legal_form", "form"))
        f["purpose"] = classify_purpose(canonical["purpose"])
        f["legal_form"] = canonical["legal_form"] or None
        card_version = _pick(service, "версия_карты", "card_version")
        if card_version is not None and not isinstance(card_version, str):
            raise InputDataError("Версия карты должна быть строкой.")
        return cls(amount, term, canonical, f, branches[0] if branches else None, valuation, card_version)
