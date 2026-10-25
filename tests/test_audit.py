"""Adversarial HTTP regressions running against Django's isolated test database."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from credit import services
from credit.pdf import make_pdf
from credit.forms import CompanyForm, DecisionForm
from credit.models import Application, Company, LoginBucket


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class AdversarialAuditTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user("audit-owner", password="audit-password-123")
        self.other = users.objects.create_user("audit-other", password="other-password-123")
        self.staff = users.objects.create_user("audit-expert", password="expert-password-123", is_staff=True)
        self.company = Company.objects.create(
            owner=self.owner, name="Листва", kind="ООО", representative="Соколов Александр Иванович",
            address="Россия, г. Москва, ул. Учебная, д. 1", inn="9900000001", ogrn="1990000000001",
            business_months=36, revenue=2400000, expenses=1600000, existing_payments=0,
        )
        self.app = self.new_app()
        self.client.force_login(self.owner)

    def new_app(self):
        return Application.objects.create(company=self.company, product="working", amount=1500000,
                                          months=24, purpose="Закупка товаров", rate=24)

    def add_document(self):
        services.upload(self.app.pk, self.owner, self.app.version, "income",
                        SimpleUploadedFile("audit.pdf", make_pdf(), content_type="application/pdf"))
        self.app.refresh_from_db()
        return self.app.documents.get()

    def submit(self):
        self.add_document()
        services.submit(self.app.pk, self.owner, self.app.version, True)
        self.app.refresh_from_db()

    def test_a01_cancelled_unsubmitted_draft_and_document_stay_private(self):
        doc = self.add_document()
        result = self.client.post(reverse("application_action", args=[self.app.pk, "cancel"]),
                                  {"version": self.app.version})
        self.assertEqual(result.status_code, 302)
        self.app.refresh_from_db()
        self.assertIsNone(self.app.consent_at)
        self.assertEqual(self.app.status, "cancelled")
        self.client.force_login(self.staff)
        for url in [reverse("application_detail", args=[self.app.pk]),
                    reverse("document_download", args=[doc.pk]),
                    reverse("payment_schedule", args=[self.app.pk]),
                    f"/api/v1/applications/{self.app.pk}/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a01_cancelled_unsubmitted_draft_is_absent_from_bank_and_audit(self):
        self.add_document()
        services.cancel(self.app.pk, self.owner, self.app.version)
        self.client.force_login(self.staff)
        for url in ["/bank/", "/bank/audit/", "/documents/", "/api/v1/applications/"]:
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), str(self.app.pk))

    def test_a02_two_drafts_do_not_overwrite_each_others_company_changes(self):
        second = self.new_app()
        stale_company = services.snapshot(self.company)
        result = self.client.post(reverse("application_edit", args=[self.app.pk, 2]),
                                  stale_company | {"name": "Обновленная компания", "version": self.app.version})
        self.assertEqual(result.status_code, 302)
        # The second tab has a different Application.version, but the same Company.
        self.client.post(reverse("application_edit", args=[second.pk, 2]),
                         stale_company | {"expenses": "1700000", "version": second.version})
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Обновленная компания")

    def test_a02_profile_stale_tab_does_not_overwrite_new_name(self):
        stale_company = services.snapshot(self.company)
        self.assertEqual(self.client.post("/profile/", stale_company | {"name": "Обновленная компания"}).status_code, 302)
        self.client.post("/profile/", stale_company | {"expenses": "1700000"})
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Обновленная компания")

    def test_a03_zero_identifiers_are_rejected(self):
        # Hardening proposal: current documented validation only checks shape.
        form = CompanyForm(services.snapshot(self.company) | {"inn": "0000000000", "ogrn": "0000000000000"})
        self.assertFalse(form.is_valid())

    def test_a03_obviously_fake_person_and_address_are_rejected(self):
        for changes in [{"representative": "---"}, {"address": "я11111111111"}]:
            with self.subTest(changes=changes):
                self.assertFalse(CompanyForm(services.snapshot(self.company) | changes).is_valid())

    def test_a05_irrelevant_hidden_offer_fields_do_not_block_approval(self):
        # UI: counteroffer -> invalid amount -> switch to approved.
        form = DecisionForm({"decision": "approved", "note": "Исходные условия проверены и одобрены.",
                             "amount": "0", "months": "24", "rate": "24"})
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_a06_reversed_unknown_action_does_not_submit_application(self):
        self.add_document()
        self.client.post(reverse("application_edit", args=[self.app.pk, 3]),
                         {"action": "upload"[::-1], "version": self.app.version, "consent": "on"})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "draft")
        self.assertIsNone(self.app.consent_at)

    def test_a07_throttled_source_cannot_keep_creating_account_buckets(self):
        self.client.logout()
        for index in range(60):
            result = self.client.post("/login/", {"username": f"missing-user-{index}", "password": "wrong"})
            self.assertEqual(result.status_code, 400)
        count = LoginBucket.objects.count()
        # A bounded reproduction, not a load test: only five blocked requests.
        for index in range(60, 65):
            result = self.client.post("/login/", {"username": f"missing-user-{index}", "password": "wrong"})
            self.assertEqual(result.status_code, 429)
        self.assertEqual(LoginBucket.objects.count(), count)

    def test_a08_staff_can_find_submitted_snapshot_after_profile_rename(self):
        self.submit()
        old_name = self.company.name
        result = self.client.post("/profile/", services.snapshot(self.company) | {"name": "Совсем другое название"})
        self.assertEqual(result.status_code, 302)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get("/bank/", {"q": old_name}), self.app.reference)

    def test_a09_calculator_uses_same_precision_as_application_form(self):
        self.assertEqual(self.client.get("/api/v1/calculator/", {
            "product": "working", "amount": "2000000.001", "months": "24",
        }).status_code, 400)

    def test_a10_invalid_product_and_terms_render_errors_without_crashing(self):
        url = reverse("application_edit", args=[self.app.pk, 1])
        for changes in [{"amount": "0"}, {"amount": "-1"}, {"months": "0"}, {"months": "121"}]:
            with self.subTest(changes=changes):
                result = self.client.post(url, {
                    "product": "gnikrow", "amount": "1500000", "months": "24",
                    "purpose": "Закупка товаров", "version": self.app.version,
                } | changes)
                self.assertEqual(result.status_code, 400)
                self.app.refresh_from_db()
                self.assertEqual(self.app.amount, Decimal("1500000"))
                self.assertEqual(self.app.months, 24)
                self.assertEqual(self.app.version, 1)

    def test_a11_replaying_the_same_new_application_form_creates_one_draft(self):
        # Two submissions of one rendered form, rather than two intentional
        # applications from independently opened forms. Preserve hidden fields
        # so a future form-level idempotency token participates in the test.
        from html.parser import HTMLParser

        class HiddenInputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.data = {}

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
                    self.data[attrs["name"]] = attrs.get("value", "")

        page = self.client.get("/applications/new/")
        self.assertEqual(page.status_code, 200)
        hidden = HiddenInputs()
        hidden.feed(page.content.decode())
        data = hidden.data | {"product": "working", "amount": "1500000", "months": "24",
                              "purpose": "Закупка товаров"}
        before = Application.objects.count()
        first = self.client.post("/applications/new/", data)
        self.assertEqual(first.status_code, 302)
        self.client.post("/applications/new/", data)
        self.assertEqual(Application.objects.count(), before + 1)

    def test_control_forged_role_status_and_rate_are_ignored(self):
        result = self.client.post("/applications/new/", {
            "product": "working", "amount": "1500000", "months": "24", "purpose": "Закупка товаров",
            "status": "funded", "is_staff": "1", "rate": "0", "company": "999999",
        })
        self.assertEqual(result.status_code, 302)
        created = Application.objects.exclude(pk=self.app.pk).get()
        self.assertEqual(created.status, "draft")
        self.assertEqual(created.rate, Decimal("24"))
        self.assertEqual(created.company_id, self.company.pk)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_staff)

    def test_control_bad_amounts_do_not_create_applications(self):
        for amount in ["", "NaN", "Infinity", "-1", "0000051", "1' OR '1'='1", "<script>alert(1)</script>", "9" * 100]:
            with self.subTest(amount=amount):
                result = self.client.post("/applications/new/", {
                    "product": "working", "amount": amount, "months": "24", "purpose": "Закупка товаров",
                })
                self.assertEqual(result.status_code, 400)
                self.assertEqual(Application.objects.count(), 1)

    def test_control_reversed_consent_does_not_submit(self):
        self.add_document()
        self.client.post(reverse("application_edit", args=[self.app.pk, 3]),
                         {"action": "submit", "version": self.app.version, "consent": "no"})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "draft")

    def test_control_other_customer_cannot_mutate_application(self):
        self.client.force_login(self.other)
        for action in ["cancel", "accept", "review", "decide"]:
            with self.subTest(action=action):
                result = self.client.post(reverse("application_action", args=[self.app.pk, action]),
                                          {"version": self.app.version, "confirm": "on", "decision": "approved"})
                self.assertEqual(result.status_code, 404)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "draft")

    def test_control_reversed_pdf_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.upload(self.app.pk, self.owner, self.app.version, "income",
                            SimpleUploadedFile("reversed.pdf", make_pdf()[::-1]))
        self.assertEqual(self.app.documents.count(), 0)
