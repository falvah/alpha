"""Deterministic scorecard: factors, policy checks, limits and pricing."""
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from credit.domain import money, payment
from .models import LoanRequest
from . import policy


def evaluate_factors(request):
    f = dict(request.features)
    reference_payment = payment(request.amount, request.term_months, policy.REFERENCE_RATE)
    income = (f["avg_monthly_inflow"] or Decimal(0)) * policy.INFLOW_HAIRCUT
    guarantor_monthly = (f["guarantor_income"] or Decimal(0)) / 12
    collateral = (f["collateral_value"] or Decimal(0)) * policy.COLLATERAL_LTV.get(f["collateral_type"], Decimal(0))
    f["pti"] = reference_payment / income if income > 0 else None
    f["guarantor_pti"] = reference_payment / guarantor_monthly if guarantor_monthly > 0 and f["has_guarantee"] else None
    f["collateral_coverage"] = collateral / request.amount if f["collateral_value"] is not None else None
    f["guarantee_coverage"] = guarantor_monthly / reference_payment if f["has_guarantee"] and guarantor_monthly else None
    factors = []

    def factor(key, title, points, label, weight=1):
        value = f.get(key)
        present = value is not None
        serialized = ("да" if value else "нет") if isinstance(value, bool) else float(value) if isinstance(value, Decimal) else value
        factors.append({"id": key, "title": title, "value": serialized, "bin": label if present else "нет данных",
                        "points": points if present else 0.15, "weight": weight, "present": present})

    for key, title in [("address_mass", "Массовость адреса"), ("address_unreliable", "Достоверность адреса"), ("director_disqualified", "Дисквалификация руководителя"), ("director_mass", "Массовость руководителя"), ("nominal_owner", "Номинальный владелец")]:
        factor(key, title, 2 if f[key] else -0.05, "выявлено" if f[key] else "не выявлено", 2)
    age = f["months_since_registration"] or 0
    factor("months_since_registration", "Возраст компании", -0.3 if age >= 24 else 0 if age >= 12 else 0.4, "≥ 24 мес." if age >= 24 else "12–23 мес." if age >= 12 else "< 12 мес.")
    factor("has_history", "Кредитная история", -0.2 if f["has_history"] else 0.3, "есть история" if f["has_history"] else "нет истории", 2)
    overdue = f["max_overdue_12m"] or 0
    factor("max_overdue_12m", "Просрочка за 12 месяцев", 2 if overdue > 90 else 0.5 if overdue else -0.2, "> 90 дней" if overdue > 90 else "1–90 дней" if overdue else "без просрочек")
    for key, title, threshold in [("companies_per_director", "Компаний у руководителя", 20), ("active_loans", "Активные кредиты", 5), ("owners_count", "Владельцы", 5), ("related_parties_count", "Связанные стороны", 10)]:
        high = (f[key] or 0) > threshold
        factor(key, title, 0.3 if high else 0, f"> {threshold}" if high else f"≤ {threshold}")
    factor("okved_section", "Раздел ОКВЭД", 0, "учтён")
    factor("legal_form", "Организационно-правовая форма", 0, "учтена")
    factor("purpose", "Цель кредита", 0.1 if f["purpose"] == "other" else 0, f["purpose"])
    flags = []
    if request.branch == "mature":
        factor("months_with_inflows", "Месяцы с поступлениями", -0.2 if (f["months_with_inflows"] or 0) >= 12 else 0.5, "≥ 12 мес." if (f["months_with_inflows"] or 0) >= 12 else "< 12 мес.", 2)
        factor("avg_monthly_inflow", "Среднемесячные поступления", 0, "поступления, не прибыль", 2)
        factor("variability", "Вариативность поступлений", 0.4 if (f["variability"] or 0) > Decimal("0.5") else -0.1, "> 0,5" if (f["variability"] or 0) > Decimal("0.5") else "≤ 0,5")
        high_pti = f["pti"] is not None and f["pti"] > policy.MAX_PTI
        factor("pti", "Платёж / поступления с дисконтом", 0.6 if high_pti else -0.2, "> 30%" if high_pti else "≤ 30%")
        budget = income * policy.MAX_PTI
    else:
        high_pti = f["guarantor_pti"] is not None and f["guarantor_pti"] > policy.MAX_PTI
        if request.branch == "young":
            flags.append("YOUNG_BUSINESS")
            for key, title in [("has_guarantee", "Наличие поручительства"), ("guarantor_income", "Годовой доход поручителя"), ("collateral_value", "Стоимость залога"), ("collateral_type", "Тип залога"), ("guarantor_pti", "Платёж / месячный доход поручителя"), ("collateral_coverage", "Покрытие залогом с LTV"), ("guarantee_coverage", "Покрытие доходом поручителя")]:
                factor(key, title, 0, "учтено")
            if not collateral:
                flags.append("NO_COLLATERAL")
            elif collateral < request.amount:
                flags.append("COLLATERAL_SHORTFALL")
        budget = guarantor_monthly * policy.MAX_PTI if f["has_guarantee"] else Decimal(0)
    if high_pti:
        flags.append("HIGH_PTI")
    if f["has_history"] is False:
        flags.append("THIN_CREDIT_HISTORY")
    if request.card_version and request.card_version != policy.VERSION:
        flags.append("CARD_VERSION_MISMATCH")
    return f, factors, flags, budget, collateral


def score_payload(payload):
    request = LoanRequest.from_payload(payload)
    f, factors, flags, budget, collateral = evaluate_factors(request)
    overdue = f["max_overdue_12m"] or 0
    points = round(sum(item["points"] for item in factors), 8)
    logit = policy.INTERCEPT + points
    pd = 1 / (1 + math.exp(-logit))
    coverage = sum(item["weight"] for item in factors if item["present"]) / sum(item["weight"] for item in factors)
    if coverage < 1:
        flags.append("PARTIAL_DATA")
    reasons = []
    for key, code in [("address_mass", "ADDRESS_MASS"), ("address_unreliable", "ADDRESS_UNRELIABLE"), ("director_disqualified", "DIRECTOR_DISQUALIFIED"), ("nominal_owner", "NOMINAL_OWNER")]:
        if f[key] is True:
            reasons.append(code)
    if f["director_mass"] or (f["companies_per_director"] or 0) > 20:
        reasons.append("MASS_MANAGEMENT")
    if overdue > 90:
        reasons.append("CRITICAL_OVERDUE")
    if request.branch is None:
        reasons.append("DOCUMENT_MISSING")
    required = ["has_history"] + (["months_with_inflows", "avg_monthly_inflow"] if request.branch == "mature" else [])
    if any(f[key] is None for key in required):
        reasons.append("MISSING_REQUIRED_DATA")
    if any(f[key] is None for key in policy.CRITICAL):
        reasons.append("CRITICAL_DATA_MISSING")
    if request.branch == "mature" and f["months_with_inflows"] is not None and f["months_with_inflows"] < 12:
        reasons.append("BRANCH_MISMATCH")
    if coverage < policy.MIN_COVERAGE:
        reasons.append("LOW_DATA_COVERAGE")
    if pd > policy.MAX_PD:
        reasons.append("HIGH_PD")
    # Invert the annuity formula without rounding the unit payment to kopecks.
    r = policy.REFERENCE_RATE / 1200
    limit = budget * (1 - (1 + r) ** -request.term_months) / r
    if request.branch == "young":
        limit = max(limit, collateral)
    limit = (limit / 10000).to_integral_value(rounding=ROUND_FLOOR) * 10000
    if min(limit, request.amount) < policy.MIN_AMOUNT:
        reasons.append("INSUFFICIENT_LIMIT")
    decision = "decline" if reasons else "review" if pd >= policy.REVIEW_PD else "approve"
    approved = rate = monthly = None
    if decision == "review":
        reasons.append("GREY_ZONE")
        flags.append("GREY_ZONE")
    if decision != "decline":
        approved = min(request.amount, limit)
        base_rate = Decimal("20") if pd < 0.05 else Decimal("22") if pd < 0.10 else Decimal("24")
        rate = ((base_rate - (1 if collateral >= approved else 0)) * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2
        monthly = payment(approved, request.term_months, rate)
        if approved < request.amount:
            flags.append("AMOUNT_REDUCED")
            reasons.append("AMOUNT_REDUCED")
        elif decision == "approve":
            reasons.append("APPROVED")
    def numeric(value):
        return float(money(value)) if value is not None else None
    return {
        "decision": decision, "is_approved": decision == "approve", "requires_manual_review": decision == "review",
        "branch": request.branch, "requested_amount": numeric(request.amount), "approved_amount": numeric(approved),
        "limit_amount": numeric(limit) if decision != "decline" else None, "annual_rate": numeric(rate), "monthly_payment": numeric(monthly),
        "term_months": request.term_months, "currency": "RUB", "pd": round(pd, 8), "logit": round(logit, 8),
        "score_points": points, "data_coverage": round(coverage, 6), "card_version": policy.VERSION,
        "reason_codes": reasons, "reasons": [{"code": code, "title": policy.REASON_TITLES[code], "type": "decline" if decision == "decline" else "review" if code == "GREY_ZONE" else "info"} for code in reasons],
        "flags": flags, "factors": factors,
        "top_risk_factors": sorted([item for item in factors if item["points"] > 0], key=lambda item: -item["points"])[:5],
        "top_mitigants": sorted([item for item in factors if item["points"] < 0], key=lambda item: item["points"])[:5],
        "simulated": True,
    }
