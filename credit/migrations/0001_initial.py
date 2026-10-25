import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="LoginBucket", fields=[
            ("key", models.CharField(max_length=64, primary_key=True, serialize=False)),
            ("failures", models.PositiveIntegerField(default=0)), ("started_at", models.DateTimeField()),
        ]),
        migrations.CreateModel(name="Company", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("kind", models.CharField(max_length=3, default="ООО", choices=[("ООО", "ООО"), ("АО", "АО"), ("ИП", "ИП"), ("КФХ", "КФХ")])),
            ("name", models.CharField(max_length=150)), ("representative", models.CharField(max_length=100)),
            ("address", models.CharField(max_length=300)), ("inn", models.CharField(max_length=12)), ("ogrn", models.CharField(max_length=15)),
            ("business_months", models.PositiveIntegerField(default=24)),
            ("revenue", models.DecimalField(max_digits=14, decimal_places=2, default=0)),
            ("expenses", models.DecimalField(max_digits=14, decimal_places=2, default=0)),
            ("existing_payments", models.DecimalField(max_digits=14, decimal_places=2, default=0)),
            ("mock_registry", models.CharField(max_length=16, default="active")), ("mock_overdue", models.BooleanField(default=False)),
            ("owner", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="company", to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name="Application", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("product", models.CharField(max_length=16, choices=[("working", "Оборотный кредит"), ("growth", "На развитие бизнеса"), ("bridge", "На кассовый разрыв")])),
            ("amount", models.DecimalField(max_digits=12, decimal_places=2)), ("months", models.PositiveIntegerField()),
            ("purpose", models.CharField(max_length=500)), ("rate", models.DecimalField(max_digits=5, decimal_places=2)),
            ("status", models.CharField(max_length=16, default="draft", db_index=True, choices=[("draft", "Черновик"), ("submitted", "На проверке"), ("review", "На рассмотрении"), ("info", "Нужны уточнения"), ("approved", "Одобрено"), ("counteroffer", "Новые условия"), ("rejected", "Не одобрено"), ("funded", "Средства выданы"), ("cancelled", "Отменена")])),
            ("company_snapshot", models.JSONField(default=dict)), ("scoring", models.JSONField(default=dict)), ("checks", models.JSONField(default=dict)),
            ("offer_amount", models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)),
            ("offer_months", models.PositiveIntegerField(null=True, blank=True)),
            ("offer_rate", models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)),
            ("decision_note", models.TextField(blank=True)), ("consent_at", models.DateTimeField(null=True, blank=True)),
            ("accepted_at", models.DateTimeField(null=True, blank=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)), ("version", models.PositiveIntegerField(default=1)),
            ("assigned_to", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL, null=True, blank=True, related_name="assigned_applications")),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="applications", to="credit.company")),
        ], options={"ordering": ["-updated_at"], "constraints": [models.CheckConstraint(condition=models.Q(amount__gt=0, months__gte=1, months__lte=120), name="valid_loan_terms")]}),
        migrations.CreateModel(name="Document", fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("kind", models.CharField(max_length=20, choices=[("income", "Выписка о доходах за 12 месяцев"), ("guarantee", "Согласие поручителя"), ("collateral", "Документы на залог")])),
            ("name", models.CharField(max_length=150)), ("content", models.BinaryField()), ("size", models.PositiveIntegerField()),
            ("sha256", models.CharField(max_length=64)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("application", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="credit.application")),
        ]),
        migrations.CreateModel(name="Event", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("from_status", models.CharField(max_length=16, blank=True)), ("to_status", models.CharField(max_length=16)),
            ("title", models.CharField(max_length=150)), ("note", models.TextField(blank=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ("application", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="credit.application")),
        ], options={"ordering": ["-created_at", "-id"]}),
        migrations.CreateModel(name="Notification", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("title", models.CharField(max_length=150)), ("text", models.TextField(blank=True)), ("read", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("application", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="credit.application")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-created_at"]}),
    ]
