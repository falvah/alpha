"""Additional regressions for form versions and compatibility of the fixes."""
from uuid import uuid4

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from credit import services
from credit.pdf import make_pdf
from credit.forms import CompanyForm
from credit.models import Application, Company


@override_settings(
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class HardeningTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('hardening-owner', password='test-password-123')
        self.staff = get_user_model().objects.create_user('hardening-staff', is_staff=True)
        self.company = Company.objects.create(owner=self.owner, name='Листва', representative='Иван Петров',
            address='Россия, Москва, ул. Учебная, 1', inn='9900000001', ogrn='1990000000001',
            business_months=36, revenue=2400000, expenses=1600000)
        self.app = Application.objects.create(company=self.company, product='working', amount=1500000,
            months=24, purpose='Закупка товаров', rate=24)
        self.client.force_login(self.owner)

    def prepared(self):
        services.upload(self.app.pk, self.owner, self.app.version, 'income',
            SimpleUploadedFile('statement.pdf', make_pdf()))
        self.app.refresh_from_db()

    def test_missing_company_version_cannot_update_profile(self):
        data = services.snapshot(self.company)
        data.pop('company_version')
        response = self.client.post('/profile/', data | {'name': 'Потерянные данные'})
        self.assertEqual(response.status_code, 400)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, 'Листва')

    def test_submission_rejects_stale_company_version(self):
        self.prepared()
        old_version = self.company.version
        self.assertEqual(self.client.post('/profile/', services.snapshot(self.company) | {'name': 'Новое имя'}).status_code, 302)
        response = self.client.post(f'/applications/{self.app.pk}/edit/3/', {
            'action':'submit', 'consent':'on', 'version':self.app.version, 'company_version':old_version}, follow=True)
        self.assertContains(response, 'Данные компании изменились.')
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'draft')
        self.company.refresh_from_db()
        self.client.post(f'/applications/{self.app.pk}/edit/3/', {
            'action':'submit', 'consent':'on', 'version':self.app.version, 'company_version':self.company.version})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'review')

    def test_missing_company_version_cannot_submit(self):
        self.prepared()
        self.client.post(f'/applications/{self.app.pk}/edit/3/', {
            'action':'submit', 'consent':'on', 'version':self.app.version})
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'draft')

    def test_cancelled_previously_submitted_application_remains_visible_to_bank(self):
        self.prepared()
        services.submit(self.app.pk, self.owner, self.app.version, True)
        self.app.refresh_from_db()
        services.cancel(self.app.pk, self.owner, self.app.version)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f'/applications/{self.app.pk}/').status_code, 200)
        self.assertContains(self.client.get('/bank/?view=history'), self.app.reference)

    def test_distinct_forms_with_identical_terms_create_independent_drafts(self):
        data = {'product':'working','amount':'1500000','months':'24','purpose':'Закупка товаров'}
        for _ in range(2):
            self.assertEqual(self.client.post('/applications/new/', data | {'creation_token':str(uuid4())}).status_code, 302)
        self.assertEqual(Application.objects.count(), 3)

    def test_malformed_creation_token_rejected(self):
        response = self.client.post('/applications/new/', {'creation_token':"<script>alert(1)</script>",
            'product':'working','amount':'1500000','months':'24','purpose':'Закупка товаров'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Application.objects.count(), 1)

    def test_short_and_numeric_company_names_remain_supported(self):
        for name in ['1С', 'Я', '123', 'Ёлка']:
            with self.subTest(name=name):
                form = CompanyForm(services.snapshot(self.company) | {'name':name})
                self.assertTrue(form.is_valid(), form.errors)

    def test_empty_profile_post_is_validation_error(self):
        response = self.client.post('/profile/', {})
        self.assertEqual(response.status_code, 400)

    def test_two_decimal_places_match_between_calculator_and_application(self):
        data = {'product':'working','amount':'1500000.01','months':'24','purpose':'Закупка товаров'}
        self.assertEqual(self.client.get('/api/v1/calculator/', data).status_code, 200)
        self.assertEqual(self.client.post('/applications/new/', data).status_code, 302)
        self.assertEqual(str(Application.objects.exclude(pk=self.app.pk).get().amount), '1500000.01')
