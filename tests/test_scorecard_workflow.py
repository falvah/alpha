import json
from decimal import Decimal
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from credit.models import Application, Company, Notification
from credit import services
from credit.scorecard_workflow import create_seed_application, evaluate_application
from .test_scorecard import example


@override_settings(SANDBOX_MODE=True, STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ScorecardWorkflowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('score-owner')
        self.staff = get_user_model().objects.create_user('score-staff', is_staff=True)
        self.other = get_user_model().objects.create_user('score-other')
        self.company = Company.objects.create(owner=self.owner, name='Тест', representative='Тестов Иван', address='Москва, улица Тестовая, 2', inn='7701234567', ogrn='1157746123456')
        self.client.force_login(self.owner)

    def test_full_three_http_journeys_and_isolated_snapshots(self):
        for name, status in [('good_mature', 'approved'), ('reduced_amount', 'counteroffer'), ('mass_address', 'rejected')]:
            with self.subTest(name=name):
                app = create_seed_application(self.owner, example(name), True, name)
                response = self.client.get(f'/applications/{app.pk}/')
                self.assertEqual(response.status_code, 200)
                app = Application.objects.get(company_snapshot__example=name)
                self.assertEqual(app.status, status)
                self.assertEqual(app.scoring_input, example(name))
                self.assertTrue(app.documents.exists())
                self.assertTrue(Notification.objects.filter(application=app).exists())
                self.assertEqual(app.events.filter(to_status='review').count(), 1)
                if status != 'rejected':
                    self.assertContains(response, 'Принять условия')
                    self.assertLessEqual(app.offer_amount, app.amount)
                    with self.assertRaises(ValidationError):
                        services.accept(app.pk, self.owner, app.version, False)
                    self.client.post(f'/applications/{app.pk}/actions/accept/', {'version': app.version, 'confirm': 'on'})
                    app.refresh_from_db()
                    self.assertEqual(app.status, 'funded')
                    services.accept(app.pk, self.owner, app.version - 1, True)
                    self.assertEqual(app.events.filter(to_status='funded').count(), 1)
                    schedule = self.client.get(f'/applications/{app.pk}/schedule/')
                    self.assertEqual(schedule.status_code, 200)
                else:
                    self.assertContains(response, 'Адрес массовой регистрации')
                    self.assertNotContains(response, 'Принять условия')
                    with self.assertRaises(ValidationError):
                        services.accept(app.pk, self.owner, app.version, True)
                api = self.client.get(f'/api/v1/applications/{app.pk}/').json()
                self.assertEqual(api['scoring']['decision'], app.scoring['decision'])
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, 'Тест')
        self.assertEqual(Application.objects.count(), 3)

    def test_consent_idempotence_and_roles(self):
        with self.assertRaises(ValidationError):
            create_seed_application(self.owner, example(), False)
        self.assertEqual(Application.objects.count(), 0)
        first = create_seed_application(self.owner, example(), True)
        second = create_seed_application(self.owner, example(), True)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Application.objects.count(), 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/applications/{first.pk}/').status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/bank/scorecard/').status_code, 200)
        self.assertContains(self.client.get(f'/applications/{first.pk}/'), 'Вероятность дефолта')
        with override_settings(SANDBOX_MODE=False), self.assertRaises(PermissionDenied):
            create_seed_application(self.owner, example(), True)

    def test_seed_is_repeatable(self):
        for _ in range(2):
            call_command('seed_scorecard', username=self.owner.username, stdout=StringIO())
        self.assertEqual(Application.objects.count(), 3)

    def test_score_api_authorization_validation_and_csrf(self):
        url = '/api/v1/scorecard/'
        self.assertEqual(self.client.post(url, example(), content_type='application/json').status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, example(), content_type='application/json')
        self.assertEqual(response.json()['approved_amount'], 1500000)
        for raw in ['[]', 'null', '{', '{"application": []}']:
            self.assertEqual(self.client.post(url, raw, content_type='application/json').status_code, 400)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        self.assertEqual(csrf.post(url, example(), content_type='application/json').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url, example(), content_type='application/json').status_code, 401)

    def test_staff_import_matches_application_and_version(self):
        from django.utils import timezone
        app = Application.objects.create(company=self.company, amount=1500000, months=24, product='working', purpose='Оборотные средства', rate=24, status='submitted', consent_at=timezone.now(), company_snapshot={'inn': self.company.inn})
        with self.assertRaises(PermissionDenied):
            evaluate_application(app.pk, self.owner, app.version, example())
        bad = example('reduced_amount')
        with self.assertRaises(ValidationError):
            evaluate_application(app.pk, self.staff, app.version, bad)
        with self.assertRaises(ValidationError):
            evaluate_application(app.pk, self.staff, 999, example())
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f'/applications/{app.pk}/scorecard/').status_code, 200)
        response = self.client.post(f'/applications/{app.pk}/scorecard/', {'version': app.version, 'payload': json.dumps(example('mass_address'))}, follow=True)
        self.assertEqual(response.status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'rejected')
        self.assertEqual(app.assigned_to, self.staff)

    def test_block_and_limit_cannot_be_bypassed(self):
        for name in ['mass_address', 'reduced_amount']:
            app = create_seed_application(self.owner, example(name), True)
            app.status = 'review'
            app.assigned_to = self.staff
            app.save()
            with self.assertRaises(ValidationError):
                services.decide(app.pk, self.staff, app.version, {'decision': 'approved', 'note': 'Попытка одобрить исходную сумму'})
            app.status = 'approved'
            app.offer_amount, app.offer_months, app.offer_rate = app.amount, app.months, Decimal(24)
            app.save()
            with self.assertRaises(ValidationError):
                services.accept(app.pk, self.owner, app.version, True)

    def test_manual_review_can_be_claimed_and_resolved(self):
        payload = example()
        payload['обогащение']['кредитная_история'].update({'есть_история': False, 'макс_просрочка_12м': 60, 'активных_кредитов': 6})
        app = create_seed_application(self.owner, payload, True)
        self.assertEqual(app.status, 'review')
        self.assertTrue(app.scoring['requires_manual_review'])
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(f'/applications/{app.pk}/'), 'Взять в работу')
        services.take_review(app.pk, self.staff, app.version)
        app.refresh_from_db()
        self.assertEqual(app.assigned_to, self.staff)
        services.decide(app.pk, self.staff, app.version, {'decision': 'counteroffer', 'note': 'Проверка завершена, предложены допустимые условия.', 'amount': Decimal(str(app.scoring['approved_amount'])), 'months': app.months, 'rate': Decimal(str(app.scoring['annual_rate']))})
        app.refresh_from_db()
        services.accept(app.pk, self.owner, app.version, True)
        app.refresh_from_db()
        self.assertEqual(app.status, 'funded')

    def test_custom_json_and_invalid_imports(self):
        self.assertEqual(self.client.post('/scenarios/', {'payload': json.dumps(example()), 'consent': 'on'}).status_code, 404)
        self.assertEqual(self.client.post('/bank/scorecard/', {'payload': json.dumps(example())}).status_code, 403)
        self.client.force_login(self.staff)
        response = self.client.post('/bank/scorecard/', {'payload': json.dumps(example())})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проверки пройдены')
        for payload in ['{', 'null', '[]']:
            self.assertEqual(self.client.post('/bank/scorecard/', {'payload': payload}).status_code, 400)
        self.assertEqual(Application.objects.count(), 0)
