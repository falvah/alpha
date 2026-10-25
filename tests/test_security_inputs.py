"""Malformed inputs, privilege boundaries and stale financial decisions."""
import copy
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from credit import services
from credit.models import Application, Company, Event
from credit.pdf import make_pdf
from credit.scorecard import InputDataError, score_payload
from credit.scorecard.payload import parse_payload
from credit.scorecard_workflow import evaluate_application
from .test_scorecard import example


@override_settings(SANDBOX_MODE=True, STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class HostileInputTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('security-owner')
        self.other = get_user_model().objects.create_user('security-other')
        self.staff = get_user_model().objects.create_user('security-staff', is_staff=True)
        self.company = Company.objects.create(owner=self.owner, name='Компания', representative='Иван Петров',
            address='Москва, улица Рабочая, 1', inn='7701234567', ogrn='1157746123456', business_months=90, revenue=900000)
        self.app = Application.objects.create(company=self.company, product='working', amount=1500000,
            months=24, rate=24, purpose='Пополнение оборотных средств')
        self.client.force_login(self.staff)

    def score(self, raw):
        return self.client.post('/api/v1/scorecard/', raw, content_type='application/json')

    def test_duplicate_json_and_aliases_rejected(self):
        for raw in ['{"application":{},"application":{}}', '{"x":1,"x":2}']:
            with self.subTest(raw=raw):
                self.assertEqual(self.score(raw).status_code, 400)
        for section, key, value in [('заявка', 'amount', 1), ('обогащение', 'address_mass', True), ('служебное', 'valuation_date', '2020-01-01')]:
            p = example()
            p[section][key] = value
            self.assertEqual(self.score(json.dumps(p)).status_code, 400)

    def test_limits_depth_and_nonfinite_values(self):
        for raw in ['[' * 1100 + '0' + ']' * 1100, '{"x":' + '[' * 9 + '0' + ']' * 9 + '}', '{"x":NaN}', '{"x":Infinity}', '{"x":"' + 'x' * 65536 + '"}', '\ud800']:
            with self.subTest(length=len(raw)):
                with self.assertRaises(InputDataError):
                    parse_payload(raw)
        self.assertEqual(self.score(b'\xff').status_code, 400)
        self.assertEqual(self.score('{"x":"' + 'x' * 65536 + '"}').status_code, 400)
        self.assertEqual(self.score('x' * (6 * 1024 * 1024 + 1)).status_code, 413)

    def test_precision_currency_and_company_age(self):
        for amount in ['1500000.001', '1руб500000', '1_500_000', True, '1e1000']:
            p = example()
            p['заявка']['сумма'] = amount
            self.assertEqual(self.score(json.dumps(p)).status_code, 400)
        p = example()
        p['обогащение']['дата_регистрации'] = '2026-10-21'
        self.assertEqual(self.score(json.dumps(p)).status_code, 400)

    def test_reversed_required_fields_and_foreign_types(self):
        p = example()
        for section, key in [('заявка','сумма'), ('заявка','срок'), ('служебное','дата_оценки')]:
            changed = copy.deepcopy(p)
            changed[section][key[::-1]] = changed[section].pop(key)
            self.assertEqual(self.score(json.dumps(changed)).status_code, 400)
        for section, key in [('заявка','сумма'), ('заявка','форма'), ('заявка','название'), ('обогащение','адрес_массовый'), ('документ','средний_приход')]:
            for value in [[], {}, ['нет'], {'value': 1}]:
                changed = copy.deepcopy(p)
                changed[section][key] = value
                with self.subTest(section=section, key=key, value=value):
                    self.assertEqual(self.score(json.dumps(changed)).status_code, 400)

    def test_bounded_leaf_mutations_never_crash(self):
        p = example()
        paths = []
        def walk(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    walk(child, path + (key,))
            else:
                paths.append(path)
        walk(p)
        values = [None, '', 'пусто', 'тен', -1, 0, True, [], {}, 'NaN', '<script>alert(1)</script>', "' OR 1=1 --"]
        for path in paths:
            for value in values:
                changed = copy.deepcopy(p)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                with self.subTest(path=path, value=value):
                    try:
                        result = score_payload(changed)
                    except InputDataError:
                        continue
                    self.assertIn(result['decision'], {'approve', 'decline', 'review'})
                    if result['decision'] == 'decline':
                        self.assertIsNone(result['approved_amount'])

    def test_customer_cannot_forge_external_checks(self):
        self.client.force_login(self.owner)
        for url in ['/bank/scorecard/', f'/applications/{self.app.pk}/scorecard/', '/api/v1/scorecard/']:
            self.assertEqual(self.client.post(url, {'payload': json.dumps(example()), 'version': 1}).status_code, 403)
        self.assertEqual(self.client.post('/scenarios/', {'payload': json.dumps(example()), 'consent': 'on'}).status_code, 404)
        self.assertEqual(Application.objects.count(), 1)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'draft')
        self.assertEqual(Event.objects.count(), 0)

    def test_host_injection_rejected(self):
        self.assertEqual(self.client.get('/health/', HTTP_HOST='attacker.invalid').status_code, 400)

    def test_xss_remains_text_and_sql_does_not_change_records(self):
        self.app.status = 'review'
        self.app.consent_at = timezone.now()
        self.app.assigned_to = self.staff
        self.app.company_snapshot = services.snapshot(self.company)
        self.app.save()
        self.client.post(f'/applications/{self.app.pk}/actions/decide/', {'version': self.app.version, 'decision': 'info', 'note': '<img src=x onerror=alert(1)> Москва 1'})
        self.client.force_login(self.owner)
        response = self.client.get(f'/applications/{self.app.pk}/')
        self.assertNotContains(response, '<img src=x')
        self.assertContains(response, '&lt;img')
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/bank/', {'q': "' OR 1=1; DROP TABLE credit_application; --"}).status_code, 200)
        self.assertEqual(Application.objects.count(), 1)

    def test_resubmission_requires_fresh_enrichment(self):
        services.upload(self.app.pk, self.owner, self.app.version, 'income', SimpleUploadedFile('statement.pdf', make_pdf()))
        self.app.refresh_from_db()
        services.submit(self.app.pk, self.owner, self.app.version, True)
        self.app.refresh_from_db()
        p = example('reduced_amount')
        p['заявка']['сумма'] = 1500000
        p['заявка']['срок'] = 24
        p['обогащение']['кредитная_история']['есть_история'] = False
        p['обогащение']['кредитная_история']['макс_просрочка_12м'] = 30
        p['обогащение']['кредитная_история']['активных_кредитов'] = 9
        p['обогащение']['собственность']['число_владельцев'] = 9
        p['документ']['средний_приход'] = 150000
        app = evaluate_application(self.app.pk, self.staff, self.app.version, p)
        self.assertEqual(app.status, 'review')
        services.decide(app.pk, self.staff, app.version, {'decision': 'info', 'note': 'Уточните финансовые показатели компании.'})
        app.refresh_from_db()
        services.submit(app.pk, self.owner, app.version, True)
        app.refresh_from_db()
        self.assertTrue(app.checks['needs_refresh'])
        services.take_review(app.pk, self.staff, app.version)
        app.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.decide(app.pk, self.staff, app.version, {'decision': 'counteroffer', 'note': 'Используем старые результаты проверки.', 'amount': Decimal(str(app.scoring['approved_amount'])), 'months': 24, 'rate': Decimal(str(app.scoring['annual_rate']))})
        app = evaluate_application(app.pk, self.staff, app.version, example())
        self.assertFalse(app.checks.get('needs_refresh'))
        self.assertEqual(app.status, 'approved')

    def test_pdf_polyglot_is_downloaded_not_executed(self):
        data = b'%PDF-1.4\n<script>alert(1)</script>\n%%EOF'
        services.upload(self.app.pk, self.owner, self.app.version, 'income', SimpleUploadedFile('../../injection.pdf', data))
        doc = self.app.documents.get()
        self.client.force_login(self.owner)
        response = self.client.get(f'/documents/{doc.pk}/download/')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertNotIn('../', doc.name)
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
