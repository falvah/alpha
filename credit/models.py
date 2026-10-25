import uuid
from django.conf import settings
from django.db import models
from .domain import STATUSES, PRODUCTS, payment

class Company(models.Model):
    version = models.PositiveIntegerField(default=1)
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="company")
    kind = models.CharField(max_length=3, choices=[(v, v) for v in ["ООО", "АО", "ИП", "КФХ"]], default="ООО")
    name = models.CharField(max_length=150)
    representative = models.CharField(max_length=100)
    address = models.CharField(max_length=300)
    inn = models.CharField(max_length=12)
    ogrn = models.CharField(max_length=15)
    business_months = models.PositiveIntegerField(default=24)
    revenue = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expenses = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    existing_payments = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    trusted_scoring_data = models.JSONField(default=dict, blank=True)
    mock_registry = models.CharField(max_length=16, default="active")
    mock_overdue = models.BooleanField(default=False)
    def __str__(self):
        return f"{self.kind} «{self.name}»"

class Application(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creation_token = models.UUIDField(null=True, blank=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="applications")
    product = models.CharField(max_length=16, choices=[(k, v["name"]) for k, v in PRODUCTS.items()])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    months = models.PositiveIntegerField()
    purpose = models.CharField(max_length=500)
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=16, choices=list(STATUSES.items()), default="draft", db_index=True)
    company_snapshot = models.JSONField(default=dict)
    scoring = models.JSONField(default=dict)
    scoring_input = models.JSONField(default=dict, blank=True)
    checks = models.JSONField(default=dict)
    offer_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    offer_months = models.PositiveIntegerField(null=True, blank=True)
    offer_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    decision_note = models.TextField(blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="assigned_applications")
    consent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0, months__gte=1, months__lte=120), name="valid_loan_terms"),
                       models.UniqueConstraint(fields=["company", "creation_token"], name="unique_company_creation_token")]
    @property
    def reference(self):
        return str(self.id)[:8].upper()
    @property
    def product_name(self):
        return PRODUCTS[self.product]["name"]
    @property
    def monthly_payment(self):
        return payment(self.offer_amount or self.amount, self.offer_months or self.months, self.offer_rate if self.offer_rate is not None else self.rate)
    @property
    def editable(self):
        return self.status in {"draft", "info"}
    @property
    def document_list(self):
        return self.documents.defer("content").order_by("created_at")

class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=20, choices=[("income", "Выписка о доходах за 12 месяцев"), ("guarantee", "Согласие поручителя"), ("collateral", "Документы на залог")])
    name = models.CharField(max_length=150)
    content = models.BinaryField()
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

class Event(models.Model):
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16)
    title = models.CharField(max_length=150)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at", "-id"]

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    title = models.CharField(max_length=150)
    text = models.TextField(blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]

class LoginBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
