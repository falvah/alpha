import os
import secrets
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from credit.pdf import make_pdf
from credit.models import Application, Company, Event
from credit import services

class Command(BaseCommand):
    help = "Создать синтетические компании и заявки. Только SANDBOX_MODE=true. Пароли генерируются случайно."
    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.SANDBOX_MODE:
            raise CommandError("seed_workspace запрещён вне тестового контура.")
        legacy_access = settings.DATA_DIR / "demo-access.txt"
        if legacy_access.exists() and not (settings.DATA_DIR / "access.txt").exists():
            legacy_access.rename(settings.DATA_DIR / "access.txt")
        User = get_user_model()
        credentials = []
        def user(login, first, last, staff=False):
            account, created = User.objects.get_or_create(username=login, defaults={"first_name": first, "last_name": last, "is_staff": staff})
            if created:
                password = secrets.token_urlsafe(18)
                account.set_password(password)
                account.save()
                credentials.append(f"{login}: {password}")
            return account
        expert = user("expert", "Мария", "Волкова", True)
        profiles = [
            ("entrepreneur", "Александр", "Соколов", "Листва", 36, 2400000, 1600000, 0, False, "working", 1500000, "submitted"),
            ("client_growth", "Елена", "Морозова", "Точка роста", 28, 3300000, 2000000, 100000, False, "growth", 5000000, "review"),
            ("client_young", "Иван", "Петров", "Новая пекарня", 7, 650000, 400000, 0, False, "working", 800000, "info"),
            ("client_risk", "Анна", "Белова", "Мастерская", 18, 700000, 680000, 40000, True, "working", 2000000, "submitted"),
        ]
        for index, (login, first, last, name, age, revenue, expenses, debt, overdue, product, amount, status) in enumerate(profiles):
            owner = user(login, first, last)
            company, created = Company.objects.get_or_create(owner=owner, defaults={"name": name, "representative": f"{last} {first} Александрович" if first in {"Александр", "Иван"} else f"{last} {first} Александровна", "address": f"Россия, г. Москва, ул. Учебная, д. {index + 1}", "inn": f"99000000{index + 1:02d}", "ogrn": f"19900000000{index + 1:02d}", "business_months": age, "revenue": revenue, "expenses": expenses, "existing_payments": debt, "mock_overdue": overdue})
            if not created:
                continue
            app = Application.objects.create(company=company, product=product, amount=amount, months=24, purpose="Закупка оборудования и товаров для развития бизнеса", rate="22.5" if product == "growth" else "24.0")
            Event.objects.create(application=app, actor=owner, to_status="draft", title="Черновик создан")
            doc_kind = "income" if age >= 12 else "guarantee"
            content = make_pdf("STATEMENT " + str(index + 1), [f"Monthly revenue: RUB {revenue}", f"Monthly expenses: RUB {expenses}", f"Existing monthly payments: RUB {debt}"])
            services.upload(app.pk, owner, app.version, doc_kind, SimpleUploadedFile("Выписка.pdf", content, "application/pdf"))
            app.refresh_from_db()
            services.submit(app.pk, owner, app.version, True)
            app.refresh_from_db()
            if status in {"review", "info"}:
                services.take_review(app.pk, expert, app.version)
                app.refresh_from_db()
            if status == "info":
                services.decide(app.pk, expert, app.version, {"decision": "info", "note": "Уточните средние ежемесячные расходы с учётом аренды нового помещения. Обновите данные на втором шаге и отправьте заявку повторно."})
            if login == "entrepreneur":
                draft = Application.objects.create(company=company, product="growth", amount=3000000, months=36, purpose="Открытие новой точки", rate="22.5")
                Event.objects.create(application=draft, actor=owner, to_status="draft", title="Черновик создан")
        if credentials:
            credential_path = settings.DATA_DIR / "access.txt"
            descriptor = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a") as stream:
                stream.write("\n".join(credentials) + "\n")
            self.stdout.write(self.style.SUCCESS(f"Начальные данные созданы. Логины и случайные пароли: {credential_path}"))
        else:
            self.stdout.write("Начальные данные уже существуют. Пароли и заявки не изменены.")
        from django.core.management import call_command
        call_command("seed_scorecard", stdout=self.stdout)
