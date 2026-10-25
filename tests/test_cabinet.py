from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from credit import services
from credit.pdf import make_pdf
from credit.forms import CompanyForm, FinancingForm
from credit.models import Application, Company, Document, Notification
from credit.demo_scoring import seed_company_source

@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CabinetTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.owner=User.objects.create_user('owner',password='test-password-123',first_name='Александр')
        self.other=User.objects.create_user('other',password='test-password-456')
        self.staff=User.objects.create_user('staff',password='staff-password-123',is_staff=True)
        self.second_staff=User.objects.create_user('staff2',password='staff-password-456',is_staff=True)
        self.company=Company.objects.create(owner=self.owner,name='Листва',kind='ООО',representative='Соколов Александр Иванович',address='Россия, г. Москва, ул. Учебная, д. 1',inn='9900000001',ogrn='1990000000001',business_months=36,revenue=2400000,expenses=1600000,existing_payments=0)
        Company.objects.create(owner=self.other,name='Другая',representative='Петров Иван',address='Россия, г. Москва, ул. Учебная, д. 2',inn='9900000002',ogrn='1990000000002')
        self.app=Application.objects.create(company=self.company,product='working',amount=1500000,months=24,purpose='Закупка товаров',rate=24)
        seed_company_source(self.company)
        self.client.force_login(self.owner)
    def upload(self, kind='income'):
        services.upload(self.app.pk,self.owner,self.app.version,kind,SimpleUploadedFile('income.pdf',make_pdf(),content_type='application/pdf'))
        self.app.refresh_from_db()
    def submit(self):
        self.upload()
        services.submit(self.app.pk,self.owner,self.app.version,True)
        self.app.refresh_from_db()
    def review(self):
        seed_company_source(self.company, grey=True)
        self.submit()
        services.take_review(self.app.pk,self.staff,self.app.version)
        self.app.refresh_from_db()
    def decide(self,decision='approved',**kwargs):
        services.decide(self.app.pk,self.staff,self.app.version,{'decision':decision,'note':'Все сведения проверены сотрудником.'}|kwargs)
        self.app.refresh_from_db()
    def test_owner_cannot_read_others_application_or_document(self):
        self.upload()
        doc=self.app.documents.first()
        self.client.force_login(self.other)
        for url in [reverse('application_detail',args=[self.app.pk]),reverse('document_download',args=[doc.pk]),f'/api/v1/applications/{self.app.pk}/']:
            self.assertEqual(self.client.get(url).status_code,404)
        with self.assertRaises(PermissionDenied): services.cancel(self.app.pk,self.other,self.app.version)
    def test_client_cannot_access_bank_or_decide(self):
        self.assertEqual(self.client.get('/bank/').status_code,403)
        self.assertEqual(self.client.post(reverse('application_action',args=[self.app.pk,'review']),{'version':1}).status_code,403)
    def test_staff_cannot_read_private_draft(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('application_detail',args=[self.app.pk])).status_code,404)
    def test_no_submission_without_consent_or_document(self):
        with self.assertRaises(ValidationError): services.submit(self.app.pk,self.owner,self.app.version,False)
        with self.assertRaises(ValidationError): services.submit(self.app.pk,self.owner,self.app.version,True)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status,'draft')
    def test_young_business_requires_guarantee(self):
        self.company.business_months=5; self.company.save()
        self.upload()
        with self.assertRaises(ValidationError): services.submit(self.app.pk,self.owner,self.app.version,True)
        services.upload(self.app.pk,self.owner,self.app.version,'guarantee',SimpleUploadedFile('guarantee.pdf',make_pdf('GUARANTEE')))
        self.app.refresh_from_db()
        services.submit(self.app.pk,self.owner,self.app.version,True)
        self.app.refresh_from_db(); self.assertEqual(self.app.status,'review')
    def test_approved_full_path_and_idempotent_acceptance(self):
        self.review(); self.decide()
        version=self.app.version
        services.accept(self.app.pk,self.owner,version,True)
        services.accept(self.app.pk,self.owner,version,True)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status,'funded')
        self.assertEqual(self.app.events.filter(to_status='funded').count(),1)
        self.assertIsNotNone(self.app.accepted_at)
        self.assertEqual(self.app.offer_amount,Decimal('1500000.00'))
    def test_clarification_resubmission(self):
        self.review(); self.decide('info')
        self.assertTrue(self.app.editable)
        services.submit(self.app.pk,self.owner,self.app.version,True)
        self.app.refresh_from_db(); self.assertEqual(self.app.status,'review')
    def test_counteroffer_terms_and_schedule(self):
        self.review(); self.decide('counteroffer',amount=Decimal(800000),months=24,rate=Decimal('29.0'))
        self.assertEqual(self.app.offer_amount,800000)
        response=self.client.get(reverse('application_detail',args=[self.app.pk]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Есть решение на других условиях')
        services.accept(self.app.pk,self.owner,self.app.version,True)
        self.assertIn('0.00',self.client.get(reverse('payment_schedule',args=[self.app.pk])).content.decode())
    def test_decision_requires_assigned_staff(self):
        self.review()
        with self.assertRaises(ValidationError): services.decide(self.app.pk,self.second_staff,self.app.version,{'decision':'approved','note':'Подтверждено сотрудником.'})
    def test_stale_version_cannot_change_status(self):
        self.submit()
        with self.assertRaises(ValidationError): services.take_review(self.app.pk,self.staff,1)
        self.app.refresh_from_db(); self.assertEqual(self.app.status,'approved')
    def test_terminal_status_cannot_change(self):
        self.review(); self.decide('rejected')
        with self.assertRaises(ValidationError): services.accept(self.app.pk,self.owner,self.app.version,True)
    def test_forged_product_terms_rejected(self):
        form=FinancingForm({'product':'bridge','amount':10000000,'months':60,'purpose':'Развитие'})
        self.assertFalse(form.is_valid()); self.assertIn('amount',form.errors); self.assertIn('months',form.errors)
    def test_no_negative_cash_inputs(self):
        data=services.snapshot(self.company)|{'revenue':-1}
        self.assertFalse(CompanyForm(data,instance=self.company).is_valid())
    def test_upload_does_not_accept_disguised_html_or_huge_files(self):
        for file in [SimpleUploadedFile('evil.pdf',b'<script>alert(1)</script>'), SimpleUploadedFile('x.pdf',b'%PDF-'+b'x'*settings.MAX_UPLOAD_SIZE+b'%%EOF')]:
            with self.assertRaises(ValidationError): services.upload(self.app.pk,self.owner,self.app.version,'income',file)
        self.assertEqual(Document.objects.count(),0)
    def test_documents_download_as_attachment(self):
        self.upload()
        response=self.client.get(reverse('document_download',args=[self.app.documents.first().pk]))
        self.assertEqual(response['Content-Type'],'application/octet-stream')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))
        self.assertEqual(response['X-Content-Type-Options'],'nosniff')
    def test_documents_locked_after_submission(self):
        self.submit()
        with self.assertRaises(PermissionDenied): services.upload(self.app.pk,self.owner,self.app.version,'income',SimpleUploadedFile('x.pdf',make_pdf()))
    def test_snapshot_not_changed_by_profile_edit(self):
        self.submit()
        self.company.revenue=10;self.company.save()
        self.app.refresh_from_db()
        self.assertEqual(Decimal(self.app.company_snapshot['revenue']),2400000)
    def test_invalid_counteroffer_has_no_partial_event(self):
        self.review();count=self.app.events.count()
        with self.assertRaises(ValidationError): self.decide('counteroffer',amount=Decimal(30000000),months=120,rate=Decimal(99))
        self.assertEqual(self.app.events.count(),count)
        self.app.refresh_from_db();self.assertEqual(self.app.status,'review')
    def test_autoescaping_staff_comment(self):
        self.review()
        services.decide(self.app.pk,self.staff,self.app.version,{'decision':'info','note':'<script>alert(1)</script>'})
        response=self.client.get(reverse('application_detail',args=[self.app.pk]))
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.assertContains(response,'&lt;script&gt;')
    def test_csrf_protects_login_and_actions(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/login/',{'username':'owner','password':'test-password-123'}).status_code,403)
        client.force_login(self.owner)
        self.assertEqual(client.post(reverse('application_action',args=[self.app.pk,'cancel']),{'version':1}).status_code,403)
    def test_anonymous_and_api_boundaries(self):
        self.client.logout()
        self.assertEqual(self.client.get('/applications/').status_code,302)
        self.assertEqual(self.client.get('/api/v1/applications/').status_code,401)
        self.assertEqual(self.client.get('/health/').status_code,200)
    def test_safe_methods_do_not_mutate(self):
        self.assertEqual(self.client.get('/logout/').status_code,405)
        self.assertEqual(self.client.get(reverse('application_action',args=[self.app.pk,'cancel'])).status_code,405)
    def test_security_headers(self):
        response=self.client.get('/')
        self.assertEqual(response['X-Frame-Options'],'DENY')
        self.assertIn("script-src 'self'",response['Content-Security-Policy'])
        self.assertEqual(response['Cache-Control'],'no-store')
    def test_all_client_pages_render(self):
        for url in ['/', '/products/', '/applications/', '/applications/new/', '/documents/', '/notifications/', '/profile/', '/help/', *[reverse('application_edit',args=[self.app.pk,step]) for step in [1,2,3]], reverse('application_detail',args=[self.app.pk])]:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code,200)
    def test_all_staff_pages_render(self):
        self.review();self.client.force_login(self.staff)
        for url in ['/bank/','/bank/audit/','/documents/',reverse('application_detail',args=[self.app.pk])]:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code,200)
    def test_calculator_bad_values_and_success(self):
        for amount in ['NaN','Infinity','-1','10000000000','abc']:
            self.assertEqual(self.client.get('/api/v1/calculator/',{'amount':amount}).status_code,400)
        self.assertEqual(self.client.get('/api/v1/calculator/',{'amount':1500000,'months':24}).status_code,200)
    def test_login_throttling(self):
        self.client.logout()
        for _ in range(10): self.client.post('/login/',{'username':'owner','password':'incorrect'})
        self.assertEqual(self.client.post('/login/',{'username':'owner','password':'incorrect'}).status_code,429)
    def test_notifications_belong_to_client(self):
        self.submit()
        self.assertTrue(Notification.objects.filter(user=self.owner).exists())
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/notifications/'),'Заявка отправлена в банк')

    def test_http_journey_from_new_application_to_disbursement(self):
        response=self.client.post('/applications/new/',{'product':'working','amount':1500000,'months':24,'purpose':'Закупка товаров'})
        self.assertEqual(response.status_code,302)
        app=Application.objects.exclude(pk=self.app.pk).get()
        response=self.client.post(reverse('application_edit',args=[app.pk,2]),services.snapshot(self.company)|{'version':app.version})
        self.assertEqual(response.status_code,302)
        app.refresh_from_db()
        response=self.client.post(reverse('application_edit',args=[app.pk,3]),{'action':'upload','version':app.version,'kind':'income','file':SimpleUploadedFile('statement.pdf',make_pdf())})
        self.assertEqual(response.status_code,302)
        app.refresh_from_db()
        response=self.client.post(reverse('application_edit',args=[app.pk,3]),{'action':'submit','version':app.version,'consent':'on','company_version':app.company.version})
        app.refresh_from_db();self.assertEqual(app.status,'approved')
        self.assertIsNone(app.assigned_to_id)
        response=self.client.post(reverse('application_action',args=[app.pk,'accept']),{'version':app.version,'confirm':'on'},follow=True)
        self.assertEqual(response.status_code,200)
        app.refresh_from_db();self.assertEqual(app.status,'funded')
        self.assertContains(response,'Платёжная интеграция не подключена')
