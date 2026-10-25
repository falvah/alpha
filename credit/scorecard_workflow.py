"""Connect scorecard input, immutable application snapshot and lending workflow."""
import hashlib
import json
import re
import uuid
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from . import services
from .pdf import make_pdf
from .domain import PRODUCTS, validate_identity
from .models import Application, Company, Document, Event
from .scorecard import score_payload, LoanRequest, InputDataError
from .scorecard.models import block

CASES = {
    "good_mature": {"title": "1. Полное одобрение", "description": "1 500 000 ₽ на 24 месяца. Проверки пройдены, сумма укладывается в лимит."},
    "reduced_amount": {"title": "2. Снижение суммы", "description": "10 000 000 ₽ на 36 месяцев. Кабинет предложит доступную сумму; её нужно принять отдельно."},
    "mass_address": {"title": "3. Блокировка", "description": "1 500 000 ₽ на 24 месяца. Массовый адрес регистрации блокирует финансирование."},
}


def load_case(name):
    if name not in CASES:
        raise InputDataError("Неизвестный набор данных.")
    path = Path(__file__).resolve().parent.parent / "fixtures" / "scorecard" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_product(request):
    product = "growth" if request.features["purpose"] in {"equipment", "investment"} else "working"
    limits = PRODUCTS[product]
    if not limits["min"] <= request.amount <= limits["max"] or request.term_months > limits["months"]:
        raise InputDataError("Сумма или срок не соответствуют продукту кабинета.")
    return product


def apply_result(app, actor):
    """Only called inside a transaction, with an already authorized input source."""
    result = app.scoring
    if app.status == "submitted":
        services.record(app, actor, "Скоринговая проверка выполнена", target="review", notify=False)
    if result["decision"] == "review":
        services.record(app, actor, "Нужна ручная проверка", "Показатели находятся в зоне ручного рассмотрения.")
        return
    target = "rejected" if result["decision"] == "decline" else "counteroffer" if "AMOUNT_REDUCED" in result["flags"] else "approved"
    note = "; ".join(reason["title"] for reason in result["reasons"])
    if target != "rejected":
        app.offer_amount = Decimal(str(result["approved_amount"]))
        app.offer_months = result["term_months"]
        app.offer_rate = Decimal(str(result["annual_rate"]))
        if target == "counteroffer":
            note += f". Доступно {app.offer_amount:,.0f} ₽. Подтвердите снижение суммы, чтобы продолжить.".replace(",", " ")
        else:
            note += ". Запрошенная сумма одобрена полностью. Подтвердите условия предложения."
    else:
        app.offer_amount = app.offer_months = app.offer_rate = None
    app.decision_note = note
    services.record(app, actor, services.STATUSES[target], note, target)


@transaction.atomic
def create_seed_application(actor, payload, consent, example_name=None):
    if not settings.SANDBOX_MODE or actor.is_staff:
        raise PermissionDenied
    if not consent:
        raise ValidationError("Подтвердите проверку данных.")
    company = Company.objects.select_for_update().get(owner=actor)
    request = LoanRequest.from_payload(payload)
    product = validate_product(request)
    a = request.application
    if not all(a.get(key) for key in ("name", "signatory", "address", "inn", "ogrn")) or not validate_identity(a["legal_form"].upper(), a["inn"], a["ogrn"]):
        raise InputDataError("Для создания заявки заполните название, подписанта, адрес и корректные ИНН / ОГРН.")
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    token = uuid.uuid5(uuid.NAMESPACE_URL, f"scorecard:{company.pk}:{digest}")
    existing = company.applications.filter(creation_token=token).first()
    if existing:
        return existing
    if company.applications.count() >= 100:
        raise ValidationError("Достигнут лимит: 100 заявок на компанию.")
    result = score_payload(payload)
    if result["approved_amount"] is not None and result["approved_amount"] < PRODUCTS[product]["min"]:
        raise InputDataError("Расчётная сумма меньше минимума выбранного продукта.")
    display_name = re.sub(r"^(?:ООО|АО|ИП)\s+", "", a["name"], flags=re.IGNORECASE).strip(' «»"')
    app = Application.objects.create(
        company=company, product=product, amount=request.amount, months=request.term_months,
        purpose=a["purpose"] or "Финансирование бизнеса", rate=result["annual_rate"] or PRODUCTS[product]["rate"],
        creation_token=token, scoring_input=payload, scoring=result, consent_at=timezone.now(),
        company_snapshot={"kind": a["legal_form"].upper(), "name": display_name, "representative": a["signatory"],
                          "address": a["address"], "inn": a["inn"], "ogrn": a["ogrn"],
                          "business_months": request.features["months_since_registration"], "source": "scorecard", "example": example_name},
        checks={"enrichment": block(payload, "обогащение", "enrichment"), "simulated": True, "valuation_date": request.valuation_date.isoformat()},
    )
    Event.objects.create(application=app, actor=actor, to_status="draft", title="Заявка создана")
    if request.branch:
        f = request.features
        lines = [f"Requested amount: RUB {request.amount}; term: {request.term_months} months."]
        if request.branch == "mature":
            lines += [f"Months with inflows: {f['months_with_inflows']}", f"Average monthly inflow: RUB {f['avg_monthly_inflow']}"]
        else:
            lines += [f"Annual guarantor income: RUB {f['guarantor_income'] or 0}", f"Collateral value: RUB {f['collateral_value'] or 0}"]
        content = make_pdf("FINANCIAL DATA " + digest[:12], lines)
        Document.objects.create(application=app, kind="income" if request.branch == "mature" else "guarantee" if request.features["has_guarantee"] else "collateral",
                                name="Выписка.pdf", content=content, size=len(content), sha256=hashlib.sha256(content).hexdigest())
    services.record(app, actor, "Заявка отправлена на скоринг", "Проверка исходных данных.", "submitted")
    apply_result(app, actor)
    return app


@transaction.atomic
def evaluate_application(pk, actor, version, payload):
    """Staff attaches trusted enrichment to an existing, consented application."""
    if not actor.is_staff:
        raise PermissionDenied
    app = services.get_locked(pk, actor)
    services.check_version(app, version)
    if not app.consent_at or app.status not in {"submitted", "review"} or app.assigned_to_id not in {None, actor.pk}:
        raise ValidationError("Скоринг доступен ответственному сотруднику для отправленной заявки.")
    request = LoanRequest.from_payload(payload)
    if request.amount != app.amount or request.term_months != app.months or request.application["inn"] != app.company_snapshot.get("inn"):
        raise ValidationError("Сумма, срок и ИНН JSON должны совпадать с отправленной заявкой.")
    app.scoring_input = payload
    app.scoring = score_payload(payload)
    if app.scoring["approved_amount"] is not None and app.scoring["approved_amount"] < PRODUCTS[app.product]["min"]:
        raise ValidationError("Расчётная сумма меньше минимума выбранного продукта.")
    app.checks = {"enrichment": block(payload, "обогащение", "enrichment"), "simulated": True, "valuation_date": request.valuation_date.isoformat()}
    app.assigned_to = actor
    services.record(app, actor, "Данные скоринговой карты получены", notify=False)
    apply_result(app, actor)
    return app
