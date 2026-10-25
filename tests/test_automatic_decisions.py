import copy
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from credit import services
from credit.forms import FinancingForm, DecisionForm
from credit.models import Application, Company, Document, Notification
from credit.scorecard import score_payload
from credit.scoring_sources import store_source
from .test_scorecard import example


@override_settings(SANDBOX_MODE=True, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AutomaticDecisionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('auto-owner')
        self.expert = get_user_model().objects.create_user('auto-expert', is_staff=True)
        self.other_expert = get_user_model().objects.create_user('auto-expert2', is_staff=True)
        self.company = Company.objects.create(owner=self.owner, name='Листва', kind='ООО',
            representative='Иван Петров', address='Россия, Москва, улица Учебная, 1',
            inn='9900000001', ogrn='1990000000001', business_months=36,
            revenue=2400000, expenses=1600000)
        self.payload = example()
        self.payload['заявка'].update({'инн': self.company.inn, 'огрн': self.company.ogrn})
        self.payload['служебное']['дата_оценки'] = timezone.localdate().isoformat()
        self.source()
        self.client.force_login(self.owner)

    def source(self, simulated=True):
        store_source(self.company, self.payload, simulated=simulated)

    def application(self, **kwargs):
        app = Application.objects.create(company=self.company, **dict(
            product='working', amount=1500000, months=24, rate=24,
            purpose='Пополнение оборотных средств', **kwargs))
        Document.objects.create(application=app, kind='income', name='income.pdf',
            content=b'%PDF-1.4\n%%EOF', size=14, sha256='a'*64)
        return app

    def submit(self, app=None):
        app = app or self.application()
        response = self.client.post(f'/applications/{app.pk}/edit/3/', {
            'action': 'submit', 'consent': 'on', 'version': app.version,
            'company_version': self.company.version,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        return app, response

    def test_immediate_approval_uses_full_model_and_no_expert_mutation(self):
        app, response = self.submit()
        self.assertEqual(app.status, 'approved')
        self.assertContains(response, 'Ваш кредит одобрен')
        self.assertIsNone(app.assigned_to_id)
        self.assertEqual(app.offer_amount, Decimal('1500000'))
        self.assertEqual(app.offer_rate, Decimal('22.5'))
        self.assertEqual(app.scoring, score_payload(app.scoring_input))
        self.assertEqual(app.scoring['card_version'], '1.0.0')
        self.assertGreater(len(app.scoring['factors']), 15)
        self.assertTrue(Notification.objects.filter(application=app, title='Автоматическое одобрение').exists())
        count = app.events.count()
        with self.assertRaises(ValidationError):
            services.take_review(app.pk, self.expert, app.version)
        self.assertEqual(app.events.count(), count)

    def test_hard_stop_decline_and_history_visibility(self):
        self.payload['обогащение']['адрес_массовый'] = True
        self.source()
        app, response = self.submit()
        self.assertEqual(app.status, 'rejected')
        self.assertContains(response, 'Адрес массовой регистрации')
        self.assertIsNone(app.offer_amount)
        self.client.force_login(self.expert)
        self.assertNotContains(self.client.get('/bank/'), app.reference)
        self.assertContains(self.client.get('/bank/?view=history&status=rejected'), app.reference)
        self.assertContains(self.client.get('/bank/audit/'), 'Автоматический отказ')

    def test_grey_zone_review_and_history_after_expert_decision(self):
        self.payload['обогащение']['кредитная_история'].update(
            {'есть_история': False, 'макс_просрочка_12м': 60, 'активных_кредитов': 6})
        self.source()
        app, response = self.submit()
        self.assertEqual(app.status, 'review')
        self.assertGreater(app.scoring['pd'], 0.12)
        self.assertLessEqual(app.scoring['pd'], 0.20)
        self.assertContains(response, 'Заявка передана эксперту')
        self.client.force_login(self.expert)
        self.assertContains(self.client.get('/bank/'), app.reference)
        services.take_review(app.pk, self.expert, app.version)
        app.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.take_review(app.pk, self.other_expert, app.version)
        services.decide(app.pk, self.expert, app.version, {'decision': 'approved', 'note': 'Данные проверены экспертом, риски допустимы.'})
        app.refresh_from_db()
        self.assertEqual(app.offer_rate, Decimal('29'))
        self.assertNotContains(self.client.get('/bank/'), app.reference)
        self.assertContains(self.client.get('/bank/?view=history'), app.reference)
        self.assertContains(self.client.get('/bank/audit/'), 'Данные проверены экспертом')

    def test_model_reduced_offer_can_be_accepted(self):
        app = self.application()
        app.amount, app.months = 10000000, 36
        app.save()
        app, _ = self.submit(app)
        self.assertEqual(app.status, 'counteroffer')
        self.assertEqual(app.offer_amount, Decimal('8310000'))
        self.assertEqual(app.offer_rate, Decimal('23.5'))
        services.accept(app.pk, self.owner, app.version, True)
        app.refresh_from_db()
        self.assertEqual(app.status, 'funded')

    def test_unknown_check_goes_to_expert_without_inventing_negative_result(self):
        self.payload['обогащение']['адрес_массовый'] = None
        self.source()
        app, response = self.submit()
        self.assertEqual(app.status, 'review')
        self.assertEqual(app.scoring['decision'], 'decline')  # original model result retained
        self.assertIn('CRITICAL_DATA_MISSING', app.scoring['reason_codes'])
        self.assertContains(response, 'Нужны данные для решения')
        services.take_review(app.pk, self.expert, app.version)
        app.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.decide(app.pk, self.expert, app.version, {'decision': 'approved', 'note': 'Попытка обойти обязательные проверки.'})

    def test_hard_stop_has_priority_over_missing_data(self):
        self.payload['обогащение'].update({'адрес_массовый': True, 'руководитель_массовый': None})
        self.source()
        app, _ = self.submit()
        self.assertEqual(app.status, 'rejected')

    def test_missing_and_expired_sources_require_refresh(self):
        for missing in [True, False]:
            with self.subTest(missing=missing):
                self.source()
                if missing:
                    self.company.trusted_scoring_data = {}
                else:
                    self.company.trusted_scoring_data['payload']['service']['valuation_date'] = '2020-01-01'
                self.company.save()
                app, _ = self.submit()
                self.assertEqual(app.status, 'review')
                self.assertTrue(app.checks['needs_refresh'])
                self.assertIsNone(app.offer_amount)

    def test_profile_changes_invalidate_source(self):
        self.company.address = 'Россия, Москва, улица Другая, 2'
        self.company.save()
        app, _ = self.submit()
        self.assertEqual(app.status, 'review')
        self.assertTrue(app.checks['needs_refresh'])

    def test_client_cannot_replace_trusted_checks(self):
        original = copy.deepcopy(self.company.trusted_scoring_data)
        response = self.client.post('/profile/', services.snapshot(self.company) | {
            'trusted_scoring_data': '{}', 'scoring': '{"decision":"approve"}',
        })
        self.assertEqual(response.status_code, 302)
        self.company.refresh_from_db()
        self.assertEqual(self.company.trusted_scoring_data, original)
        app, _ = self.submit()
        self.assertEqual(app.status, 'approved')

    def test_resubmission_requires_new_revision_and_recomputes_model(self):
        app = self.application()
        app.status = 'info'
        app.checks = {'source_revision': self.company.trusted_scoring_data['revision']}
        app.save()
        app = services.submit(app.pk, self.owner, app.version, True)
        self.assertEqual(app.status, 'review')
        self.assertTrue(app.checks['needs_refresh'])
        services.take_review(app.pk, self.expert, app.version)
        app.refresh_from_db()
        services.decide(app.pk, self.expert, app.version, {'decision': 'info', 'note': 'Обновите результаты проверки документов.'})
        app.refresh_from_db()
        self.source()
        app = services.submit(app.pk, self.owner, app.version, True)
        self.assertEqual(app.status, 'approved')
        self.assertFalse(app.checks.get('needs_refresh'))

    @override_settings(SANDBOX_MODE=False)
    def test_only_trusted_non_simulated_sources_work_outside_sandbox(self):
        app = self.application()
        result = services.submit(app.pk, self.owner, app.version, True)
        self.assertEqual(result.status, 'review')
        self.source(simulated=False)
        app = self.application()
        result = services.submit(app.pk, self.owner, app.version, True)
        self.assertEqual(result.status, 'approved')

    def test_wrong_company_source_cannot_be_imported(self):
        self.payload['заявка']['инн'] = '9900000002'
        with self.assertRaises(ValidationError):
            self.source()

    def test_all_loan_amount_widgets_increment_by_thousand(self):
        self.assertEqual(FinancingForm().fields['amount'].widget.attrs['step'], '1000')
        self.assertEqual(DecisionForm().fields['amount'].widget.attrs['step'], '1000')
        self.assertContains(self.client.get('/products/'), 'step="1000"')
