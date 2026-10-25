import csv
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from decimal import InvalidOperation
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from . import services
from .domain import PRODUCTS, STATUSES, payment, schedule
from .forms import CompanyForm, DecisionForm, FinancingForm, UploadForm, ScorecardForm
from .models import Application, Company, Document, Event, LoginBucket, Notification

def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped

def visible(user):
    qs = Application.objects.select_related("company", "company__owner", "assigned_to")
    return qs.filter(consent_at__isnull=False).exclude(status="draft") if user.is_staff else qs.filter(company__owner=user)

def client_company(user):
    if user.is_staff or not hasattr(user, "company"):
        raise PermissionDenied
    return user.company

@require_http_methods(["GET", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = None
    if request.method == "POST":
        username = request.POST.get("username", "")[:150].strip()
        password = request.POST.get("password", "")[:1024]
        now = timezone.now()
        keys = [hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest() for value in ["account:" + username.casefold(), "ip:" + request.META.get("REMOTE_ADDR", "unknown")]]
        blocked = False
        with transaction.atomic():
            LoginBucket.objects.filter(started_at__lt=now - timedelta(days=1)).delete()
            # Check the source first: blocked requests must not create arbitrary account rows.
            for key, limit in [(keys[1], 60), (keys[0], 10)]:
                bucket, _ = LoginBucket.objects.get_or_create(key=key, defaults={"started_at": now})
                if bucket.started_at < now - timedelta(minutes=15):
                    bucket.failures, bucket.started_at = 0, now
                if bucket.failures >= limit:
                    blocked = True
                    break
                bucket.failures = min(bucket.failures + 1, 1000)
                bucket.save()
        if blocked:
            return render(request, "registration/login.html", {"error": "Слишком много попыток. Попробуйте через 15 минут."}, status=429)
        user = authenticate(request, username=username, password=password)
        if user is not None:
            LoginBucket.objects.filter(key=keys[0]).delete()
            login(request, user)
            return redirect("dashboard")
        error = "Не удалось войти. Проверьте логин и пароль."
    return render(request, "registration/login.html", {"error": error}, status=400 if error else 200)

@require_POST
def sign_out(request):
    logout(request)
    return redirect("login")

@login_required
@require_GET
def dashboard(request):
    if request.user.is_staff:
        return redirect("staff_queue")
    company = client_company(request.user)
    apps = visible(request.user)
    active = apps.exclude(status__in=["funded", "rejected", "cancelled"])
    attention = active.filter(status__in=["info", "approved", "counteroffer"]).first()
    return render(request, "credit/dashboard.html", {"company": company, "applications": apps[:3], "active_count": active.count(),
        "funded_total": apps.filter(status="funded").aggregate(total=Sum("offer_amount"))["total"] or 0,
        "attention": attention, "latest": active.first(), "products": PRODUCTS, "articles": news_for(company, apps)})

def news_for(company, apps):
    result = []
    if apps.filter(status="info").exists():
        result.append({"tag": "По вашей заявке", "title": "Банк ждёт уточнений", "text": "Откройте заявку, прочитайте комментарий сотрудника и дополните данные. Вся история сохранится.", "icon": "document"})
    if company.business_months < 12:
        result.append({"tag": "Молодому бизнесу", "title": "Бизнесу меньше года?", "text": "Вместо выписки за 12 месяцев потребуется согласие поручителя или документ на залог.", "icon": "growth"})
    else:
        result.append({"tag": "Подготовка к заявке", "title": "Все документы под рукой", "text": "Подготовьте PDF-выписку о доходах за 12 месяцев. Добавить её можно на последнем шаге заявки.", "icon": "document"})
    product = apps.first().product if apps.exists() else "working"
    result.append({"tag": "Для вашего бизнеса", "title": "Планируйте платёж заранее", "text": f"Вы рассматриваете «{PRODUCTS[product]['name']}». Калькулятор покажет платёж и график, чтобы сопоставить их с денежным потоком.", "icon": "chart"})
    return result[:3]

@login_required
@require_GET
def products(request):
    client_company(request.user)
    return render(request, "credit/products.html", {"products": PRODUCTS})

@login_required
@require_GET
def applications(request):
    client_company(request.user)
    status = request.GET.get("status", "")
    qs = visible(request.user)
    if status in STATUSES:
        qs = qs.filter(status=status)
    return render(request, "credit/applications.html", {"page": Paginator(qs, 10).get_page(request.GET.get("page")), "selected_status": status, "statuses": STATUSES})

@login_required
@require_http_methods(["GET", "POST"])
def new_application(request):
    company = client_company(request.user)
    product = request.GET.get("product", "working")
    if product not in PRODUCTS:
        product = "working"
    initial = {"product": product, "amount": 1500000, "months": 24 if product != "bridge" else 6, "purpose": PRODUCTS[product]["purpose"]}
    # Carry a valid calculator selection forward without trusting query parameters.
    candidate = FinancingForm({**initial, "amount": request.GET.get("amount", initial["amount"]), "months": request.GET.get("months", initial["months"])})
    if candidate.is_valid():
        initial.update(candidate.cleaned_data)
    creation_token = request.POST.get("creation_token") if request.method == "POST" else str(uuid.uuid4())
    if creation_token:
        try:
            creation_token = str(uuid.UUID(creation_token))
        except (ValueError, AttributeError):
            return HttpResponse("Некорректный ключ формы. Обновите страницу.", status=400)
    form = FinancingForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            Company.objects.select_for_update().get(pk=company.pk)
            if creation_token:
                existing = company.applications.filter(creation_token=creation_token).first()
                if existing:
                    return redirect("application_detail", pk=existing.pk)
            if company.applications.count() >= 100:
                form.add_error(None, "Достигнут лимит: 100 заявок на компанию.")
            elif company.applications.filter(status="draft").count() >= 20:
                form.add_error(None, "Сохранено 20 черновиков. Продолжите один из них или отмените лишний.")
            else:
                app = form.save(commit=False)
                app.creation_token = creation_token or None
                app.company, app.rate = company, PRODUCTS[form.cleaned_data["product"]]["rate"]
                app.save()
                Event.objects.create(application=app, actor=request.user, to_status="draft", title="Черновик создан")
                return redirect("application_edit", pk=app.pk, step=2)
    return render(request, "credit/wizard.html", {"form": form, "step": 1, "products": PRODUCTS, "creation_token": creation_token or str(uuid.uuid4())}, status=400 if form.errors else 200)

@login_required
@require_http_methods(["GET", "POST"])
def edit_application(request, pk, step):
    client_company(request.user)
    app = get_object_or_404(visible(request.user), pk=pk)
    if not app.editable or step not in [1, 2, 3]:
        return redirect("application_detail", pk=pk)
    form = FinancingForm(instance=app) if step == 1 else CompanyForm(instance=app.company) if step == 2 else UploadForm()
    if request.method == "POST":
        try:
            if step == 3:
                if request.POST.get("action") == "upload":
                    form = UploadForm(request.POST, request.FILES)
                    if form.is_valid():
                        services.upload(pk, request.user, request.POST.get("version"), form.cleaned_data["kind"], form.cleaned_data["file"])
                        messages.success(request, "Документ добавлен")
                        return redirect("application_edit", pk=pk, step=3)
                elif request.POST.get("action") == "submit":
                    result = services.submit(pk, request.user, request.POST.get("version"), request.POST.get("consent") == "on", request.POST.get("company_version", ""))
                    messages.success(request, f"{result.get_status_display()}. {result.decision_note}")
                    return redirect("application_detail", pk=pk)
                else:
                    return HttpResponse("Неизвестное действие. Обновите страницу и повторите запрос.", status=400)
            else:
                with transaction.atomic():
                    app = services.get_locked(pk, request.user)
                    services.check_version(app, request.POST.get("version"))
                    if not app.editable:
                        raise PermissionDenied
                    if step == 2:
                        app.company = Company.objects.select_for_update().get(pk=app.company_id)
                    form = FinancingForm(request.POST, instance=app) if step == 1 else CompanyForm(request.POST, instance=app.company)
                    if form.is_valid():
                        form.save()
                        if step == 1:
                            app.rate = PRODUCTS[app.product]["rate"]
                        services.record(app, request.user, "Параметры обновлены" if step == 1 else "Данные компании обновлены", notify=False)
                        return redirect("application_edit", pk=pk, step=step + 1)
                    # ModelForm validation mutates its instance even when invalid.
                    # Render the persisted loan summary, keeping the bound field errors.
                    app.refresh_from_db()
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("application_edit", pk=pk, step=step)
    return render(request, "credit/wizard.html", {"form": form, "application": app, "step": step, "products": PRODUCTS}, status=400 if form.errors else 200)

@login_required
@require_GET
def application_detail(request, pk):
    app = get_object_or_404(visible(request.user), pk=pk)
    return render(request, "credit/detail.html", detail_context(app))

def detail_context(app, decision_form=None):
    score = app.scoring
    initial = {"decision": "counteroffer" if "AMOUNT_REDUCED" in score.get("flags", []) else "approved", "amount": score.get("approved_amount"), "months": score.get("term_months"), "rate": score.get("annual_rate")}
    return {"application": app, "decision_form": decision_form if decision_form is not None else DecisionForm(initial=initial), "events": app.events.select_related("actor"),
               "schedule": schedule(app.offer_amount or app.amount, app.offer_months or app.months, app.offer_rate if app.offer_rate is not None else app.rate)}

@login_required
@require_POST
def application_action(request, pk, action):
    app = get_object_or_404(visible(request.user), pk=pk)
    version = request.POST.get("version")
    try:
        if action == "review":
            services.take_review(pk, request.user, version)
        elif action == "decide":
            if not request.user.is_staff:
                raise PermissionDenied
            form = DecisionForm(request.POST)
            if not form.is_valid():
                return render(request, "credit/detail.html", detail_context(app, form), status=400)
            services.decide(pk, request.user, version, form.cleaned_data)
        elif action == "accept":
            services.accept(pk, request.user, version, request.POST.get("confirm") == "on")
        elif action == "cancel":
            services.cancel(pk, request.user, version)
        else:
            return HttpResponse(status=404)
        messages.success(request, "Изменения сохранены")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("application_detail", pk=app.pk)

@login_required
@require_POST
def document_delete(request, pk, doc_id):
    get_object_or_404(visible(request.user), pk=pk)
    get_object_or_404(Document, pk=doc_id, application_id=pk)
    try:
        services.delete_document(pk, doc_id, request.user, request.POST.get("version"))
        messages.success(request, "Документ удалён")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("application_edit", pk=pk, step=3)

@login_required
@require_GET
def document_download(request, doc_id):
    doc = get_object_or_404(Document.objects.filter(application__in=visible(request.user)).defer("content"), pk=doc_id)
    from django.utils.http import content_disposition_header
    response = HttpResponse(bytes(doc.content), content_type="application/octet-stream")
    response["Content-Disposition"] = content_disposition_header(True, doc.name)
    response["X-Content-Type-Options"] = "nosniff"
    return response

@login_required
@require_GET
def payment_schedule(request, pk):
    app = get_object_or_404(visible(request.user), pk=pk)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="schedule-{app.reference}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Месяц", "Платёж, руб.", "Основной долг", "Проценты", "Остаток"])
    for row in schedule(app.offer_amount or app.amount, app.offer_months or app.months, app.offer_rate if app.offer_rate is not None else app.rate):
        writer.writerow(row.values())
    return response

@login_required
@require_GET
def documents(request):
    qs = Document.objects.filter(application__in=visible(request.user)).defer("content").select_related("application").order_by("-created_at")
    return render(request, "credit/documents.html", {"page": Paginator(qs, 15).get_page(request.GET.get("page"))})

@login_required
@require_http_methods(["GET", "POST"])
def notifications(request):
    qs = Notification.objects.filter(user=request.user)
    if request.method == "POST":
        qs.update(read=True)
        return redirect("notifications")
    return render(request, "credit/notifications.html", {"page": Paginator(qs, 15).get_page(request.GET.get("page"))})

@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    company = client_company(request.user)
    if request.method == "POST":
        with transaction.atomic():
            company = Company.objects.select_for_update().get(pk=company.pk)
            form = CompanyForm(request.POST, instance=company)
            if form.is_valid():
                form.save()
                messages.success(request, "Данные сохранены. Уже отправленные заявки остаются без изменений.")
                return redirect("profile")
    else:
        form = CompanyForm(instance=company)
    return render(request, "credit/profile.html", {"form": form}, status=400 if form.errors else 200)

@login_required
@require_GET
def help_page(request):
    return render(request, "credit/help.html")

@staff_required
@require_GET
def staff_queue(request):
    qs = visible(request.user)
    counts = {k: qs.filter(status=k).count() for k in ["submitted", "review", "info", "funded", "approved", "rejected"]}
    queue_view = "history" if request.GET.get("view") == "history" else "review"
    if queue_view == "review":
        qs = qs.filter(status__in=["submitted", "review", "info"])
    query = request.GET.get("q", "")[:100].strip()
    status = request.GET.get("status", "")
    if query:
        qs = qs.filter(Q(company_snapshot__name__icontains=query) | Q(company_snapshot__inn__icontains=query))
    if status in STATUSES:
        qs = qs.filter(status=status)
    if request.GET.get("mine") == "1":
        qs = qs.filter(assigned_to=request.user)
    return render(request, "credit/staff.html", {"page": Paginator(qs.order_by("-updated_at", "pk"), 15).get_page(request.GET.get("page")), "counts": counts, "query": query, "selected_status": status, "statuses": STATUSES if queue_view == "history" else {k: STATUSES[k] for k in ["submitted", "review", "info"]}, "queue_view": queue_view})

@staff_required
@require_GET
def audit(request):
    qs = Event.objects.select_related("actor", "application", "application__company").filter(application__in=visible(request.user))
    return render(request, "credit/audit.html", {"page": Paginator(qs, 30).get_page(request.GET.get("page"))})

@require_GET
def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})

def api_user(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "authentication_required"}, status=401)
        return view(request, *args, **kwargs)
    return wrapped

def serialize(app):
    return {"id": str(app.id), "reference": app.reference, "product": app.product, "amount": str(app.amount), "months": app.months,
            "status": app.status, "status_label": app.get_status_display(), "version": app.version, "payment": str(app.monthly_payment),
            "offer": {"amount": str(app.offer_amount), "months": app.offer_months, "rate": str(app.offer_rate)} if app.offer_amount else None,
            "decision_note": app.decision_note, "scoring": app.scoring if app.scoring.get("card_version") else None, "updated_at": app.updated_at.isoformat()}


@staff_required
@require_http_methods(["GET", "POST"])
def scorecard_workspace(request):
    from .scorecard import score_payload
    form = ScorecardForm(request.POST if request.method == "POST" else None)
    result = score_payload(form.cleaned_data["payload"]) if request.method == "POST" and form.is_valid() else None
    return render(request, "credit/scorecard_workspace.html", {"form": form, "result": result}, status=400 if form.errors else 200)


@staff_required
@require_http_methods(["GET", "POST"])
def application_scorecard(request, pk):
    from .scorecard_workflow import evaluate_application
    from .scorecard import InputDataError
    app = get_object_or_404(visible(request.user), pk=pk)
    form = ScorecardForm(request.POST if request.method == "POST" else None, initial={"payload": json.dumps(app.scoring_input or {}, ensure_ascii=False, indent=2)})
    if request.method == "POST" and form.is_valid():
        try:
            evaluate_application(pk, request.user, request.POST.get("version"), form.cleaned_data["payload"])
            return redirect("application_detail", pk=pk)
        except (InputDataError, ValidationError) as exc:
            form.add_error(None, str(exc))
    return render(request, "credit/scorecard_form.html", {"application": app, "form": form}, status=400 if form.errors else 200)


@api_user
@require_POST
def api_scorecard(request):
    from .scorecard import score_payload, InputDataError
    from .scorecard.payload import parse_payload
    if not request.user.is_staff:
        raise PermissionDenied
    try:
        result = score_payload(parse_payload(request.body))
    except InputDataError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(result)

@api_user
@require_GET
def api_products(request):
    return JsonResponse({"products": PRODUCTS, "simulated": True})

@api_user
@require_GET
def api_applications(request, pk=None):
    if pk:
        return JsonResponse(serialize(get_object_or_404(visible(request.user), pk=pk)))
    page = Paginator(visible(request.user), 20).get_page(request.GET.get("page"))
    return JsonResponse({"results": [serialize(a) for a in page], "page": page.number, "pages": page.paginator.num_pages})

@api_user
@require_GET
def api_calculate(request):
    try:
        product = PRODUCTS[request.GET.get("product", "working")]
        form = FinancingForm({"product": request.GET.get("product", "working"), "amount": request.GET.get("amount", "1500000"),
                              "months": request.GET.get("months", "24"), "purpose": product["purpose"]})
        if not form.is_valid():
            raise ValueError
        amount, months = form.cleaned_data["amount"], form.cleaned_data["months"]
        rows = schedule(amount, months, product["rate"])
        return JsonResponse({"payment": str(payment(amount, months, product["rate"])), "total": str(sum(r["payment"] for r in rows)), "rate": product["rate"], "simulated": True})
    except (KeyError, ValueError, InvalidOperation):
        return JsonResponse({"error": "Проверьте сумму и срок для выбранного продукта."}, status=400)
