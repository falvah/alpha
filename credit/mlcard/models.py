"""Входные модели заявки и адаптер из JSON бэкенда.

Внутренние имена — английские, ключи бэкенда — русские. Вся адаптация
сосредоточена в :meth:`LoanRequest.from_payload`, чтобы ядро скоринга не
зависело от формата транспорта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from .normalization import (
    InputDataError,
    is_blank,
    normalize_collateral_type,
    parse_count,
    parse_date,
    parse_decimal,
    parse_int,
    parse_tristate,
)


@dataclass(frozen=True, slots=True)
class Application:
    """Заявка на кредит."""

    amount: Decimal
    term_months: int
    legal_form: str | None = None
    name: str | None = None
    signatory: str | None = None
    address: str | None = None
    inn: str | None = None
    ogrn: str | None = None
    purpose: str | None = None
    document_url: str | None = None


@dataclass(frozen=True, slots=True)
class CreditHistory:
    has_history: bool | None = None
    max_overdue_12m: int | None = None
    active_loans: int | None = None


@dataclass(frozen=True, slots=True)
class Ownership:
    owners_count: int | None = None
    nominal_owner: bool | None = None


@dataclass(frozen=True, slots=True)
class Enrichment:
    """Обогащение из внешних источников."""

    okved: str | None = None
    address_mass: bool | None = None
    address_unreliable: bool | None = None
    director_disqualified: bool | None = None
    director_mass: bool | None = None
    companies_per_director: int | None = None
    registration_date: date | None = None
    credit_history: CreditHistory = field(default_factory=CreditHistory)
    ownership: Ownership = field(default_factory=Ownership)
    related_parties_count: int | None = None


@dataclass(frozen=True, slots=True)
class MatureDocument:
    """Ветка документов: история ≥ 12 месяцев."""

    months_with_inflows: int | None = None
    avg_monthly_inflow: Decimal | None = None
    variability: float | None = None


@dataclass(frozen=True, slots=True)
class YoungDocument:
    """Ветка документов: история < 12 месяцев."""

    has_guarantee: bool | None = None
    guarantor_income: Decimal | None = None
    collateral_value: Decimal | None = None
    collateral_type: str | None = None


Document = MatureDocument | YoungDocument


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    valuation_date: date
    card_version: str | None = None


@dataclass(frozen=True, slots=True)
class LoanRequest:
    application: Application
    enrichment: Enrichment
    document: Document | None
    service: ServiceInfo

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LoanRequest":
        """Собирает заявку из JSON бэкенда (русские ключи)."""
        if not isinstance(payload, Mapping):
            raise InputDataError("payload: ожидался объект")

        application_raw = _mapping(
            _pick(payload, "заявка", "application", "application_data"), "заявка"
        )
        enrichment_raw = _mapping(
            _pick(payload, "обогащение", "enrichment", "enrichment_data"), "обогащение"
        )
        service_raw = _mapping(_pick(payload, "служебное", "service"), "служебное")

        application = _parse_application(application_raw)
        valuation_date = parse_date(
            _pick(service_raw, "дата_оценки", "valuation_date"), field="служебное.дата_оценки"
        )
        if valuation_date is None:
            raise InputDataError("служебное.дата_оценки: обязательное поле")

        enrichment = _parse_enrichment(enrichment_raw, valuation_date)
        document = _parse_document(_pick(payload, "документ", "document"))

        card_version = _optional_str(_pick(service_raw, "версия_карты", "card_version"))

        return cls(
            application=application,
            enrichment=enrichment,
            document=document,
            service=ServiceInfo(valuation_date=valuation_date, card_version=card_version),
        )


_MATURE_KEYS = ("месяцев_с_поступлениями", "средний_приход", "вариативность")
_YOUNG_KEYS = ("есть_поручительство", "доход_поручителя", "стоимость_залога", "тип_залога")


def _pick(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InputDataError(f"{name}: ожидался объект, получено {type(value).__name__}")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_application(raw: Mapping[str, Any]) -> Application:
    amount = parse_decimal(_pick(raw, "сумма", "amount"), field="заявка.сумма")
    if amount is None:
        raise InputDataError("заявка.сумма: обязательное поле")
    if amount <= 0:
        raise InputDataError("заявка.сумма: должна быть больше нуля")

    term = parse_int(_pick(raw, "срок", "term", "term_months"), field="заявка.срок")
    if term is None:
        raise InputDataError("заявка.срок: обязательное поле")
    if term <= 0:
        raise InputDataError("заявка.срок: должен быть больше нуля")

    return Application(
        amount=amount,
        term_months=term,
        legal_form=_optional_str(_pick(raw, "форма", "legal_form", "form")),
        name=_optional_str(_pick(raw, "название", "name")),
        signatory=_optional_str(_pick(raw, "в_лице", "signatory")),
        address=_optional_str(_pick(raw, "адрес", "address")),
        inn=_optional_str(_pick(raw, "инн", "inn")),
        ogrn=_optional_str(_pick(raw, "огрн", "ogrn")),
        purpose=_optional_str(_pick(raw, "цель", "purpose")),
        document_url=_optional_str(_pick(raw, "ссылка_на_документ", "document_url")),
    )


def _parse_enrichment(raw: Mapping[str, Any], valuation_date: date) -> Enrichment:
    registration_date = parse_date(
        _pick(raw, "дата_регистрации", "registration_date"),
        field="обогащение.дата_регистрации",
    )
    if registration_date is not None and registration_date > valuation_date:
        raise InputDataError("обогащение.дата_регистрации: дата позже даты оценки")

    history_raw = _mapping(
        _pick(raw, "кредитная_история", "credit_history"), "обогащение.кредитная_история"
    )
    ownership_raw = _mapping(
        _pick(raw, "собственность", "ownership"), "обогащение.собственность"
    )
    related_raw = _mapping(
        _pick(raw, "связанные_стороны", "related_parties"), "обогащение.связанные_стороны"
    )

    history = CreditHistory(
        has_history=parse_tristate(
            _pick(history_raw, "есть_история", "has_history"),
            field="обогащение.кредитная_история.есть_история",
        ),
        max_overdue_12m=parse_count(
            _pick(history_raw, "макс_просрочка_12м", "max_overdue_12m"),
            field="обогащение.кредитная_история.макс_просрочка_12м",
        ),
        active_loans=parse_count(
            _pick(history_raw, "активных_кредитов", "active_loans"),
            field="обогащение.кредитная_история.активных_кредитов",
        ),
    )

    ownership = Ownership(
        owners_count=parse_count(
            _pick(ownership_raw, "число_владельцев", "owners_count"),
            field="обогащение.собственность.число_владельцев",
        ),
        nominal_owner=parse_tristate(
            _pick(ownership_raw, "номинальный_владелец", "nominal_owner"),
            field="обогащение.собственность.номинальный_владелец",
        ),
    )

    related_count = parse_count(
        _pick(
            related_raw,
            "число_связанных",
            "related_parties_count",
        ),
        field="обогащение.связанные_стороны.число_связанных",
    )
    if related_count is None:
        # Допускаем, когда число пришло не во вложенном объекте.
        related_count = parse_count(
            _pick(raw, "число_связанных", "related_parties_count"),
            field="обогащение.число_связанных",
        )

    okved_raw = _pick(raw, "оквэд", "okved")
    okved = None if okved_raw is None else str(okved_raw).strip() or None

    return Enrichment(
        okved=okved,
        address_mass=parse_tristate(
            _pick(raw, "адрес_массовый", "address_mass"), field="обогащение.адрес_массовый"
        ),
        address_unreliable=parse_tristate(
            _pick(raw, "адрес_недостоверный", "address_unreliable"),
            field="обогащение.адрес_недостоверный",
        ),
        director_disqualified=parse_tristate(
            _pick(raw, "руководитель_дисквалиф.", "руководитель_дисквалифицирован",
                  "director_disqualified"),
            field="обогащение.руководитель_дисквалиф.",
        ),
        director_mass=parse_tristate(
            _pick(raw, "руководитель_массовый", "director_mass"),
            field="обогащение.руководитель_массовый",
        ),
        companies_per_director=parse_count(
            _pick(raw, "юрлиц_у_руководителя", "companies_per_director"),
            field="обогащение.юрлиц_у_руководителя",
        ),
        registration_date=registration_date,
        credit_history=history,
        ownership=ownership,
        related_parties_count=related_count,
    )


def _parse_document(raw: Any) -> Document | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise InputDataError("документ: ожидался объект")
    if not raw:
        return None

    mature_present = any(not is_blank(raw.get(key)) for key in _MATURE_KEYS if key in raw)
    young_present = any(not is_blank(raw.get(key)) for key in _YOUNG_KEYS if key in raw)

    if mature_present and young_present:
        raise InputDataError(
            "документ: одновременно заполнены поля веток ≥12 мес и <12 мес"
        )
    if not mature_present and not young_present:
        return None

    if mature_present:
        months = parse_int(
            _pick(raw, "месяцев_с_поступлениями", "months_with_inflows"),
            field="документ.месяцев_с_поступлениями",
        )
        inflow = parse_decimal(
            _pick(raw, "средний_приход", "avg_monthly_inflow"),
            field="документ.средний_приход",
        )
        variability_number = parse_decimal(
            _pick(raw, "вариативность", "variability"), field="документ.вариативность"
        )
        if months is not None and months < 0:
            raise InputDataError("документ.месяцев_с_поступлениями: отрицательное значение")
        if inflow is not None and inflow < 0:
            raise InputDataError("документ.средний_приход: отрицательное значение")
        if variability_number is not None and variability_number < 0:
            raise InputDataError("документ.вариативность: отрицательное значение")
        return MatureDocument(
            months_with_inflows=months,
            avg_monthly_inflow=inflow,
            variability=float(variability_number) if variability_number is not None else None,
        )

    guarantee = parse_tristate(
        _pick(raw, "есть_поручительство", "has_guarantee"), field="документ.есть_поручительство"
    )
    income = parse_decimal(
        _pick(raw, "доход_поручителя", "guarantor_income"), field="документ.доход_поручителя"
    )
    collateral = parse_decimal(
        _pick(raw, "стоимость_залога", "collateral_value"), field="документ.стоимость_залога"
    )
    if income is not None and income < 0:
        raise InputDataError("документ.доход_поручителя: отрицательное значение")
    if collateral is not None and collateral < 0:
        raise InputDataError("документ.стоимость_залога: отрицательное значение")

    return YoungDocument(
        has_guarantee=guarantee,
        guarantor_income=income,
        collateral_value=collateral,
        collateral_type=normalize_collateral_type(
            _pick(raw, "тип_залога", "collateral_type")
        ),
    )
