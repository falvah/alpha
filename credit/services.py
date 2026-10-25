import hashlib
import re
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .domain import PRODUCTS, STATUSES, ensure_transition
from .models import Application, Company, Document, Event, Notification

def check_version(application, version):
    if str(application.version) != str(version):
        raise ValidationError("Заявка была изменена в другой вкладке. Обновите страницу и повторите действие.")

def get_locked(pk, actor):
    app = Application.objects.select_for_update().select_related("company", "company__owner").get(pk=pk)
    if not actor.is_staff and app.company.owner_id != actor.id:
        raise PermissionDenied
    return app

def record(app, actor, title, note="", target=None, notify=True):
    previous = app.status
    if target:
        try:
            ensure_transition(previous, target)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        app.status = target
    app.version += 1
    app.save()
    Event.objects.create(application=app, actor=actor, from_status=previous, to_status=app.status, title=title, note=note)
    if notify:
        Notification.objects.create(user=app.company.owner, application=app, title=title, text=note)

def snapshot(company):
    return {key: str(getattr(company, key)) for key in ["kind", "name", "representative", "address", "inn", "ogrn", "business_months", "revenue", "expenses", "existing_payments"]} | {"company_version": str(company.version)}

def calculate_score(app):
    from .automatic_decisions import calculate
    calculate(app)

@transaction.atomic
def submit(pk, actor, version, consent, company_version=None):
    app = get_locked(pk, actor)
    check_version(app, version)
    if actor.is_staff or not app.editable:
        raise PermissionDenied
    if not consent:
        raise ValidationError("Подтвердите согласие на обработку данных и тестовую проверку кредитной истории.")
    if company_version is not None and str(company_version) != str(app.company.version):
        raise ValidationError("Данные компании изменились. Проверьте второй шаг и повторите отправку.")
    app.company = Company.objects.select_for_update().get(pk=app.company_id)
    from .forms import CompanyForm, FinancingForm
    cf = CompanyForm(snapshot(app.company), instance=app.company)
    ff = FinancingForm({k: getattr(app, k) for k in ["product", "amount", "months", "purpose"]}, instance=app)
    if not cf.is_valid() or not ff.is_valid():
        raise ValidationError("Проверьте сведения о компании и параметры кредита на первых двух шагах.")
    kinds = set(app.documents.values_list("kind", flat=True))
    if app.company.business_months >= 12 and "income" not in kinds:
        raise ValidationError("Добавьте выписку о доходах за последние 12 месяцев.")
    if app.company.business_months < 12 and not kinds.intersection({"guarantee", "collateral"}):
        raise ValidationError("Для бизнеса младше 12 месяцев добавьте согласие поручителя или документы на залог.")
    app.company_snapshot = snapshot(app.company)
    app.consent_at = timezone.now()
    app.decision_note = ""
    calculate_score(app)
    record(app, actor, "Заявка отправлена в банк", "Документы получены. Выполняется автоматическая проверка.", "submitted", notify=False)
    from .automatic_decisions import apply
    apply(app, actor)
    return app

@transaction.atomic
def upload(pk, actor, version, kind, file):
    app = get_locked(pk, actor)
    check_version(app, version)
    if actor.is_staff or not app.editable:
        raise PermissionDenied
    if kind not in {"income", "guarantee", "collateral"}:
        raise ValidationError("Выберите допустимый тип документа.")
    if app.documents.count() >= 5:
        raise ValidationError("К одной заявке можно приложить до 5 документов. Удалите лишний файл.")
    if file.size > settings.MAX_UPLOAD_SIZE:
        raise ValidationError("Максимальный размер PDF — 5 МБ.")
    used = Document.objects.filter(application__company=app.company).aggregate(total=Sum("size"))["total"] or 0
    if used + file.size > 100 * 1024 * 1024:
        raise ValidationError("Достигнут лимит документов компании: 100 МБ. Удалите ненужные файлы из черновиков.")
    content = file.read(settings.MAX_UPLOAD_SIZE + 1)
    if len(content) > settings.MAX_UPLOAD_SIZE or not file.name.lower().endswith(".pdf") or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
        raise ValidationError("Выберите PDF-файл размером до 5 МБ.")
    # Files are untrusted binary objects; never parsed/executed/rendered by the service.
    digest = hashlib.sha256(content).hexdigest()
    if app.documents.filter(sha256=digest).exists():
        raise ValidationError("Этот файл уже прикреплён.")
    name = re.sub(r"[^\w\s.()\-]", "_", file.name.replace("\\", "/").split("/")[-1])[:140]
    Document.objects.create(application=app, kind=kind, name=name, content=content, size=len(content), sha256=digest)
    record(app, actor, "Документ добавлен", name, notify=False)

@transaction.atomic
def delete_document(pk, doc_id, actor, version):
    app = get_locked(pk, actor)
    check_version(app, version)
    if actor.is_staff or not app.editable:
        raise PermissionDenied
    doc = app.documents.get(pk=doc_id)
    name = doc.name
    doc.delete()
    record(app, actor, "Документ удалён", name, notify=False)

@transaction.atomic
def take_review(pk, actor, version):
    if not actor.is_staff:
        raise PermissionDenied
    app = get_locked(pk, actor)
    check_version(app, version)
    if app.status not in {"submitted", "review"} or app.assigned_to_id not in {None, actor.pk}:
        raise ValidationError("В работу можно взять только заявку, ожидающую рассмотрения и не назначенную другому эксперту.")
    if app.status == "review" and app.assigned_to_id is None:
        app.assigned_to = actor
        record(app, actor, "Сотрудник назначен на ручную проверку", "Проверяем показатели скоринговой карты.")
        return
    app.assigned_to = actor
    record(app, actor, "Сотрудник рассматривает заявку", "Проверяем данные и подбираем условия финансирования.", "review")

@transaction.atomic
def decide(pk, actor, version, data):
    if not actor.is_staff:
        raise PermissionDenied
    app = get_locked(pk, actor)
    check_version(app, version)
    if app.status != "review" or app.assigned_to_id != actor.id:
        raise ValidationError("Решение доступно только ответственному сотруднику для заявки на рассмотрении.")
    decision = data["decision"]
    if decision in {"approved", "counteroffer"}:
        amount = data["amount"] if decision == "counteroffer" else app.amount
        months = data["months"] if decision == "counteroffer" else app.months
        rate = data["rate"] if decision == "counteroffer" else Decimal(str(app.scoring.get("annual_rate") or app.rate))
        p = PRODUCTS[app.product]
        if not p["min"] <= amount <= p["max"] or not 1 <= months <= p["months"] or not Decimal("1") <= rate <= Decimal("60"):
            raise ValidationError("Условия не соответствуют ограничениям выбранного продукта.")
        validate_scored_offer(app, amount, months, rate)
        app.offer_amount, app.offer_months, app.offer_rate = amount, months, rate
    app.decision_note = data["note"]
    record(app, actor, STATUSES[decision], data["note"], decision)

@transaction.atomic
def accept(pk, actor, version, confirmed):
    app = get_locked(pk, actor)
    if actor.is_staff:
        raise PermissionDenied
    # Retrying the same acceptance never creates a second disbursement event.
    if app.status == "funded":
        return app
    check_version(app, version)
    if not confirmed:
        raise ValidationError("Подтвердите, что ознакомились с условиями и принимаете предложение.")
    if app.status not in {"approved", "counteroffer"}:
        raise ValidationError("Выдача доступна только после одобрения условий.")
    validate_scored_offer(app, app.offer_amount, app.offer_months, app.offer_rate)
    app.accepted_at = timezone.now()
    record(app, actor, "Условия приняты", "Условия приняты и зафиксированы в кабинете. Платёжная интеграция не подключена; реальные деньги не переводились.", "funded")
    return app

def validate_scored_offer(app, amount, months, rate):
    result = app.scoring
    if not result.get("card_version"):
        return
    if app.checks.get("needs_refresh"):
        raise ValidationError("После уточнения заявки необходимо заново проверить данные скоринговой карты.")
    if result["decision"] == "decline" or result.get("approved_amount") is None:
        raise ValidationError("Финансирование заблокировано результатом скоринговых проверок.")
    if amount is None or rate is None or amount > Decimal(str(result["approved_amount"])) or months != result["term_months"] or rate < Decimal(str(result["annual_rate"])):
        raise ValidationError("Предложение выходит за условия скоринга. Выполните новый расчёт.")

@transaction.atomic
def cancel(pk, actor, version):
    app = get_locked(pk, actor)
    if actor.is_staff:
        raise PermissionDenied
    check_version(app, version)
    record(app, actor, "Заявка отменена", "Вы можете создать новую заявку с другими параметрами.", "cancelled")
