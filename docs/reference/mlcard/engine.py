"""Ядро экспертного скоринга: факторы → PD → решение → лимит и ставка.

Модель детерминированная и не делает сетевых вызовов: на вход приходит
нормализованная заявка, на выход — решение с полной расшифровкой.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from typing import Any, Mapping

from .config import CardConfig, ConfigError, Factor
from .models import LoanRequest, MatureDocument, YoungDocument
from .normalization import (
    classify_purpose,
    months_between,
    normalize_legal_form,
    normalize_text,
    parse_okved_section,
)

TWO_PLACES = Decimal("0.01")

_FEATURE_TITLES = {
    "months_since_registration": "дата регистрации",
    "has_credit_history": "кредитная история",
    "months_with_inflows": "месяцы с поступлениями",
    "avg_monthly_inflow": "средний приход",
    "guarantee_present": "поручительство",
    "collateral_coverage": "обеспечение",
    "address_mass": "массовость адреса",
    "address_unreliable": "недостоверность адреса",
    "director_disqualified": "дисквалификация руководителя",
    "director_mass": "массовость руководителя",
    "nominal_owner": "номинальность владельца",
}

_DISPLAY_VALUES = {
    "purpose": {
        "working_capital": "пополнение оборотных средств",
        "equipment": "оборудование/транспорт",
        "refinancing": "рефинансирование",
        "investment": "инвестиции/строительство",
        "other": "прочее",
    },
    "legal_form": {"ооо": "ООО", "ао": "АО", "ип": "ИП"},
}


@dataclass(frozen=True, slots=True)
class FactorResult:
    id: str
    title: str
    value: Any
    label: str
    points: float
    weight: float
    present: bool


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    title: str
    kind: str


@dataclass(frozen=True, slots=True)
class Decision:
    decision: str
    branch: str
    requested_amount: Decimal
    approved_amount: Decimal | None
    limit_amount: Decimal | None
    annual_rate: float | None
    monthly_payment: Decimal | None
    term_months: int
    pd: float
    logit: float
    score_points: float
    data_coverage: float
    currency: str
    card_version: str
    reasons: tuple[Reason, ...]
    flags: tuple[str, ...]
    factors: tuple[FactorResult, ...]

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    def to_dict(self) -> dict[str, Any]:
        """JSON-совместимое представление для бэкенда."""
        return {
            "decision": self.decision,
            "is_approved": self.approved,
            "requires_manual_review": self.decision == "review",
            "branch": self.branch,
            "requested_amount": _money_to_float(self.requested_amount),
            "approved_amount": _money_to_float(self.approved_amount),
            "limit_amount": _money_to_float(self.limit_amount),
            "annual_rate": self.annual_rate,
            "monthly_payment": _money_to_float(self.monthly_payment),
            "term_months": self.term_months,
            "currency": self.currency,
            "pd": round(self.pd, 4),
            "logit": round(self.logit, 4),
            "score_points": round(self.score_points, 4),
            "data_coverage": round(self.data_coverage, 4),
            "card_version": self.card_version,
            "reason_codes": [reason.code for reason in self.reasons],
            "reasons": [
                {"code": reason.code, "title": reason.title, "type": reason.kind}
                for reason in self.reasons
            ],
            "flags": list(self.flags),
            "factors": [
                {
                    "id": factor.id,
                    "title": factor.title,
                    "value": _json_value(factor.value),
                    "bin": factor.label,
                    "points": round(factor.points, 4),
                    "weight": factor.weight,
                    "present": factor.present,
                }
                for factor in self.factors
            ],
            "top_risk_factors": [
                _factor_brief(factor)
                for factor in sorted(
                    (f for f in self.factors if f.points > 0),
                    key=lambda f: f.points,
                    reverse=True,
                )[:5]
            ],
            "top_mitigants": [
                _factor_brief(factor)
                for factor in sorted(
                    (f for f in self.factors if f.points < 0),
                    key=lambda f: f.points,
                )[:5]
            ],
        }


@dataclass(frozen=True, slots=True)
class _Context:
    features: dict[str, Any]
    branch: str
    monthly_income: Decimal | None
    collateral_value: Decimal | None
    collateral_type: str | None


@dataclass(frozen=True, slots=True)
class _Terms:
    limit: Decimal
    approved: Decimal | None
    rate: float
    payment: Decimal | None


class Scorer:
    """Скорер, привязанный к версии карты."""

    def __init__(self, config: CardConfig | None = None) -> None:
        self.config = config or CardConfig.load_default()

    def score(self, request: LoanRequest) -> Decision:
        context = self._build_context(request)
        factors = self._evaluate_factors(context.features)
        score_points = sum(factor.points for factor in factors)
        logit = self.config.intercept + score_points
        pd = _sigmoid(logit)

        applicable_weight = sum(factor.weight for factor in factors)
        present_weight = sum(factor.weight for factor in factors if factor.present)
        coverage = present_weight / applicable_weight if applicable_weight else 0.0

        reasons: list[Reason] = []
        flags: list[str] = []

        if (
            request.service.card_version
            and request.service.card_version != self.config.version
        ):
            flags.append("CARD_VERSION_MISMATCH")

        validation = self._validate_policy(request, context)
        reasons.extend(Reason(code, title, "policy") for code, title in validation)

        stops = [
            stop for stop in self.config.hard_stops if _matches(stop.when, context.features)
        ]
        reasons.extend(Reason(stop.code, stop.title, "stop") for stop in stops)

        required_missing = self._required_missing(context)
        if required_missing:
            names = ", ".join(
                _FEATURE_TITLES.get(feature, feature) for feature in required_missing
            )
            reasons.append(
                Reason(
                    "MISSING_REQUIRED_DATA",
                    f"Нет обязательных данных: {names}",
                    "policy",
                )
            )

        critical_missing = [
            feature
            for feature in self.config.policy.critical_features
            if context.features.get(feature) is None
        ]
        if critical_missing:
            names = ", ".join(
                _FEATURE_TITLES.get(feature, feature) for feature in critical_missing
            )
            reasons.append(
                Reason(
                    "CRITICAL_DATA_MISSING",
                    f"Не проведены обязательные проверки: {names}",
                    "policy",
                )
            )

        if coverage < self.config.policy.min_coverage:
            reasons.append(
                Reason(
                    "LOW_DATA_COVERAGE",
                    f"Заполнено {coverage:.0%} факторов, требуется "
                    f"{self.config.policy.min_coverage:.0%}",
                    "policy",
                )
            )

        blocked = bool(
            validation
            or stops
            or required_missing
            or critical_missing
            or coverage < self.config.policy.min_coverage
        )

        decision = "decline"
        if not blocked:
            if pd > self.config.policy.max_pd:
                if (
                    self.config.policy.review_pd is not None
                    and pd <= self.config.policy.review_pd
                ):
                    decision = "review"
                    reasons.append(
                        Reason(
                            "MANUAL_REVIEW",
                            f"PD {pd:.1%} в серой зоне — требуется ручное решение",
                            "info",
                        )
                    )
                    flags.append("GREY_ZONE")
                else:
                    reasons.append(
                        Reason(
                            "PD_TOO_HIGH",
                            f"PD {pd:.1%} выше порога {self.config.policy.max_pd:.1%}",
                            "risk",
                        )
                    )
            else:
                decision = "approve"

        terms = self._compute_terms(request, context, pd) if decision != "decline" else None
        if decision != "decline":
            if terms is None:
                decision = "decline"
                reasons.append(
                    Reason(
                        "NO_REPAYMENT_SOURCE",
                        "Нет подтверждённого источника погашения",
                        "policy",
                    )
                )
            elif terms.approved is None or terms.approved < self.config.policy.min_amount:
                decision = "decline"
                reasons.append(
                    Reason(
                        "AMOUNT_BELOW_MIN",
                        "Доступный лимит ниже минимальной суммы кредита",
                        "policy",
                    )
                )

        if decision == "decline":
            # Если серая зона не дошла до оферты, не показываем её как причину.
            reasons = [reason for reason in reasons if reason.code != "MANUAL_REVIEW"]
            flags = [flag for flag in flags if flag != "GREY_ZONE"]
            approved_amount = None
            annual_rate = None
            payment = None
            limit_amount = terms.limit if terms is not None else None
        else:
            assert terms is not None and terms.approved is not None
            approved_amount = terms.approved
            annual_rate = terms.rate
            payment = terms.payment
            limit_amount = terms.limit
            if decision == "approve":
                reasons.insert(0, Reason("APPROVED", "Одобрено", "info"))
                if approved_amount < request.application.amount:
                    flags.append("AMOUNT_REDUCED")

        if context.branch == "young":
            flags.append("YOUNG_BUSINESS")
        if context.features.get("has_credit_history") is False:
            flags.append("THIN_CREDIT_HISTORY")
        pti = context.features.get("pti")
        if pti is None:
            pti = context.features.get("guarantor_pti")
        if pti is not None and pti > self.config.policy.pti_limit:
            flags.append("HIGH_PTI")
        collateral_coverage = context.features.get("collateral_coverage")
        if context.branch == "young":
            if collateral_coverage is not None and collateral_coverage < 1.0:
                flags.append("COLLATERAL_SHORTFALL")
            if collateral_coverage is None:
                flags.append("NO_COLLATERAL")
        if coverage < 1.0:
            flags.append("PARTIAL_DATA")

        return Decision(
            decision=decision,
            branch=context.branch,
            requested_amount=request.application.amount,
            approved_amount=approved_amount,
            limit_amount=limit_amount,
            annual_rate=annual_rate,
            monthly_payment=payment,
            term_months=request.application.term_months,
            pd=pd,
            logit=logit,
            score_points=score_points,
            data_coverage=coverage,
            currency=self.config.policy.currency,
            card_version=self.config.version,
            reasons=tuple(reasons),
            flags=tuple(dict.fromkeys(flags)),
            factors=factors,
        )

    # ------------------------------------------------------------------ features
    def _build_context(self, request: LoanRequest) -> _Context:
        application = request.application
        enrichment = request.enrichment
        document = request.document
        policy = self.config.policy

        if isinstance(document, MatureDocument):
            branch = "mature"
        elif isinstance(document, YoungDocument):
            branch = "young"
        else:
            branch = "none"

        features: dict[str, Any] = {
            "branch": branch,
            "requested_amount": float(application.amount),
            "term_months": application.term_months,
            "legal_form": normalize_legal_form(application.legal_form),
            "purpose": classify_purpose(application.purpose),
            "okved_section": parse_okved_section(enrichment.okved),
            "months_since_registration": (
                months_between(enrichment.registration_date, request.service.valuation_date)
                if enrichment.registration_date is not None
                else None
            ),
            "address_mass": enrichment.address_mass,
            "address_unreliable": enrichment.address_unreliable,
            "director_disqualified": enrichment.director_disqualified,
            "director_mass": enrichment.director_mass,
            "companies_per_director": enrichment.companies_per_director,
            "has_credit_history": enrichment.credit_history.has_history,
            "max_overdue_12m": enrichment.credit_history.max_overdue_12m,
            "active_loans": enrichment.credit_history.active_loans,
            "owners_count": enrichment.ownership.owners_count,
            "nominal_owner": enrichment.ownership.nominal_owner,
            "related_parties": enrichment.related_parties_count,
            "months_with_inflows": None,
            "avg_monthly_inflow": None,
            "inflow_variability": None,
            "pti": None,
            "guarantee_present": None,
            "guarantee_coverage": None,
            "collateral_coverage": None,
            "collateral_type": None,
            "guarantor_pti": None,
        }

        reference_payment = _annuity_payment(
            application.amount, policy.reference_rate, application.term_months
        )
        monthly_income: Decimal | None = None
        collateral_value: Decimal | None = None
        collateral_type: str | None = None

        if isinstance(document, MatureDocument):
            features["months_with_inflows"] = document.months_with_inflows
            features["inflow_variability"] = document.variability
            if document.avg_monthly_inflow is not None:
                features["avg_monthly_inflow"] = float(document.avg_monthly_inflow)
                monthly_income = document.avg_monthly_inflow * Decimal(
                    str(policy.inflow_haircut)
                )
                if monthly_income > 0:
                    features["pti"] = float(reference_payment / monthly_income)
                else:
                    monthly_income = None

        elif isinstance(document, YoungDocument):
            features["guarantee_present"] = document.has_guarantee
            features["collateral_type"] = document.collateral_type
            collateral_value = document.collateral_value
            collateral_type = document.collateral_type

            if collateral_value is not None and application.amount > 0:
                features["collateral_coverage"] = float(
                    collateral_value / application.amount
                )

            if document.has_guarantee is True and document.guarantor_income is not None:
                coverage = document.guarantor_income / application.amount
                features["guarantee_coverage"] = float(coverage)
                if document.guarantor_income > 0:
                    monthly_income = document.guarantor_income / Decimal(12)
                    features["guarantor_pti"] = float(reference_payment / monthly_income)

        return _Context(
            features=features,
            branch=branch,
            monthly_income=monthly_income,
            collateral_value=collateral_value,
            collateral_type=collateral_type,
        )

    # ------------------------------------------------------------------- factors
    def _evaluate_factors(self, features: Mapping[str, Any]) -> tuple[FactorResult, ...]:
        branch = str(features["branch"])
        results: list[FactorResult] = []
        for factor in self.config.factors:
            if not factor.applies(branch):
                continue
            value = features.get(factor.source)
            if value is None:
                results.append(
                    FactorResult(
                        id=factor.id,
                        title=factor.title,
                        value=None,
                        label="нет данных",
                        points=factor.missing_points,
                        weight=factor.weight,
                        present=False,
                    )
                )
                continue

            points, label = self._factor_points(factor, value)
            results.append(
                FactorResult(
                    id=factor.id,
                    title=factor.title,
                    value=value,
                    label=label,
                    points=points,
                    weight=factor.weight,
                    present=True,
                )
            )
        return tuple(results)

    def _factor_points(self, factor: Factor, value: Any) -> tuple[float, str]:
        if factor.kind == "tri":
            if not isinstance(value, bool):
                raise ConfigError(f"factor {factor.id}: ожидался bool, получено {value!r}")
            points = factor.yes_points if value else factor.no_points
            assert points is not None
            return points, "да" if value else "нет"

        if factor.kind == "range":
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"factor {factor.id}: ожидалось число, получено {value!r}"
                ) from exc
            for bin_ in factor.bins:
                if bin_.matches(number):
                    return bin_.points, bin_.label
            raise ConfigError(
                f"factor {factor.id}: значение {number!r} не попало ни в один бин"
            )

        if factor.kind == "category":
            key = normalize_text(value)
            points = factor.categories.get(key, factor.default_points)
            if points is None:
                raise ConfigError(f"factor {factor.id}: нет балла для категории {value!r}")
            label = _DISPLAY_VALUES.get(factor.id, {}).get(str(value), str(value))
            return points, label

        if factor.kind == "okved":
            section = int(value)
            for section_config in factor.sections:
                if section_config.min <= section <= section_config.max:
                    return section_config.points, section_config.label
            if factor.default_points is None:
                raise ConfigError(f"factor {factor.id}: нет default_points")
            return factor.default_points, f"ОКВЭД {section:02d}"

        raise ConfigError(f"factor {factor.id}: неизвестный тип {factor.kind!r}")

    # -------------------------------------------------------------------- policy
    def _validate_policy(
        self, request: LoanRequest, context: _Context
    ) -> list[tuple[str, str]]:
        policy = self.config.policy
        errors: list[tuple[str, str]] = []
        amount = request.application.amount
        term = request.application.term_months
        if amount < policy.min_amount or amount > policy.max_amount:
            errors.append(
                (
                    "AMOUNT_OUT_OF_POLICY",
                    f"Сумма {amount} вне диапазона "
                    f"{policy.min_amount}–{policy.max_amount} {policy.currency}",
                )
            )
        if term < policy.min_term_months or term > policy.max_term_months:
            errors.append(
                (
                    "TERM_OUT_OF_POLICY",
                    f"Срок {term} мес. вне диапазона "
                    f"{policy.min_term_months}–{policy.max_term_months}",
                )
            )
        if context.branch == "none":
            errors.append(
                (
                    "DOCUMENT_MISSING",
                    "Не переданы данные документа о движении средств или обеспечения",
                )
            )
        return errors

    def _required_missing(self, context: _Context) -> list[str]:
        required = list(
            dict.fromkeys(
                (*self.config.required_features.get("all", ()),
                 *self.config.required_features.get(context.branch, ()))
            )
        )
        return [feature for feature in required if context.features.get(feature) is None]

    # --------------------------------------------------------------------- terms
    def _compute_terms(
        self, request: LoanRequest, context: _Context, pd: float
    ) -> _Terms | None:
        policy = self.config.policy
        rate = self._rate(pd, context)

        income_limit: Decimal | None = None
        if context.monthly_income is not None and context.monthly_income > 0:
            max_payment = context.monthly_income * Decimal(str(policy.pti_limit))
            income_limit = _annuity_principal(
                max_payment, rate, request.application.term_months
            )

        collateral_limit: Decimal | None = None
        if (
            context.branch == "young"
            and context.collateral_value is not None
            and context.collateral_value > 0
        ):
            key = normalize_text(context.collateral_type or "прочее")
            ltv = policy.collateral_ltv.get(key, policy.collateral_ltv_default)
            collateral_limit = context.collateral_value * Decimal(str(ltv))

        candidates = [
            limit for limit in (income_limit, collateral_limit) if limit is not None
        ]
        if not candidates:
            return None

        limit = min(candidates).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if limit > policy.max_amount:
            limit = policy.max_amount

        requested = request.application.amount
        if requested <= limit:
            approved = requested
        else:
            step = policy.amount_rounding
            approved = (limit / step).to_integral_value(rounding=ROUND_FLOOR) * step
        approved = approved.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        payment: Decimal | None = None
        if approved >= policy.min_amount:
            payment = _annuity_payment(
                approved, rate, request.application.term_months
            ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        return _Terms(
            limit=limit,
            approved=approved if approved >= policy.min_amount else None,
            rate=rate,
            payment=payment,
        )

    def _rate(self, pd: float, context: _Context) -> float:
        policy = self.config.policy
        rate = policy.base_rate + policy.rate_premium(pd)

        if (
            context.branch == "young"
            and context.collateral_value is not None
            and context.collateral_value > 0
        ):
            key = normalize_text(context.collateral_type or "прочее")
            rate -= policy.collateral_discount.get(
                key, policy.collateral_discount.get("прочее", 0.0)
            )

        rate = max(policy.min_rate, min(policy.max_rate, rate))
        step = Decimal(str(policy.rate_rounding))
        rounded = (
            (Decimal(str(rate)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            * step
        )
        return float(rounded)


# --------------------------------------------------------------------- helpers
def score_payload(
    payload: Mapping[str, Any], config: CardConfig | None = None
) -> dict[str, Any]:
    """Удобная точка входа: JSON бэкенда → JSON решения."""
    return Scorer(config).score(LoanRequest.from_payload(payload)).to_dict()


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _annuity_payment(
    principal: Decimal, annual_rate: float, months: int
) -> Decimal:
    """Аннуитетный платёж; annual_rate — в процентах годовых."""
    if months <= 0:
        raise ValueError("months must be positive")
    with localcontext() as ctx:
        ctx.prec = 28
        monthly_rate = Decimal(str(annual_rate)) / Decimal(100) / Decimal(12)
        if monthly_rate == 0:
            return principal / Decimal(months)
        growth = (Decimal(1) + monthly_rate) ** months
        return principal * monthly_rate * growth / (growth - Decimal(1))


def _annuity_principal(
    payment: Decimal, annual_rate: float, months: int
) -> Decimal:
    """Максимальный основной долг по допустимому аннуитетному платежу."""
    if months <= 0:
        raise ValueError("months must be positive")
    with localcontext() as ctx:
        ctx.prec = 28
        monthly_rate = Decimal(str(annual_rate)) / Decimal(100) / Decimal(12)
        if monthly_rate == 0:
            return payment * Decimal(months)
        growth = (Decimal(1) + monthly_rate) ** months
        return payment * (Decimal(1) - Decimal(1) / growth) / monthly_rate


def _matches(node: Mapping[str, Any], features: Mapping[str, Any]) -> bool:
    if "all" in node:
        return all(_matches(child, features) for child in node["all"])
    if "any" in node:
        return any(_matches(child, features) for child in node["any"])

    actual = features.get(node["field"])
    op = node["op"]
    expected = node.get("value")

    if op == "is_null":
        return actual is None
    if op == "is_not_null":
        return actual is not None
    if op == "is_true":
        return actual is True
    if op == "is_false":
        return actual is False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op in {"gt", "gte", "lt", "lte"}:
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        return actual <= expected
    raise ConfigError(f"Неизвестная операция предиката: {op!r}")


def _money_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def _json_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return round(value, 4)
    return value


def _factor_brief(factor: FactorResult) -> dict[str, Any]:
    return {
        "id": factor.id,
        "title": factor.title,
        "bin": factor.label,
        "points": round(factor.points, 4),
    }


# Публичный алиас для интеграции: score(request, card) без создания Scorer.
def score(request: LoanRequest, config: CardConfig | None = None) -> Decision:
    return Scorer(config).score(request)
