"""Server-owned scoring inputs, independent of editable customer form fields."""
import copy
import hashlib
import json
import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from .scorecard import LoanRequest, InputDataError
from .scorecard.engine import canonical_payload


PROFILE_FIELDS = ('kind', 'name', 'representative', 'address', 'inn', 'ogrn',
                  'business_months', 'revenue', 'expenses', 'existing_payments')


def profile_identity(company):
    return {key: str(Decimal(str(getattr(company, key))).quantize(Decimal('0.01')))
            if key in {'revenue', 'expenses', 'existing_payments'} else str(getattr(company, key))
            for key in PROFILE_FIELDS}


def store_source(company, payload, *, simulated=False):
    """Only server-side import/seed/expert code may call this, never customer POST."""
    request = LoanRequest.from_payload(payload)
    if request.application['inn'] != company.inn or request.application['ogrn'] != company.ogrn:
        raise ValidationError('ИНН и ОГРН источника должны совпадать с компанией.')
    if simulated and not settings.SANDBOX_MODE:
        raise ValidationError('Демонстрационные данные недоступны вне тестового контура.')
    canonical = json.loads(json.dumps(canonical_payload(payload), default=str))
    company.trusted_scoring_data = {
        'payload': canonical, 'simulated': simulated, 'profile': profile_identity(company),
        'revision': str(uuid.uuid4()), 'imported_at': timezone.now().isoformat(),
    }
    company.save(update_fields=['trusted_scoring_data'])


def application_input(app):
    source = app.company.trusted_scoring_data
    reason = ''
    payload = copy.deepcopy(source.get('payload', {}))
    if not payload:
        reason = 'Нужны результаты проверок и структурированные показатели документов.'
    elif source.get('simulated') and not settings.SANDBOX_MODE:
        reason = 'Демонстрационные проверки нельзя использовать вне тестового контура.'
    elif source.get('profile') != profile_identity(app.company):
        reason = 'Сведения компании изменились: требуется обновить исходные данные скоринга.'
    else:
        try:
            request = LoanRequest.from_payload(payload)
        except InputDataError:
            reason = 'Исходные данные скоринга противоречивы: требуется проверка источника.'
        else:
            age = (timezone.localdate() - request.valuation_date).days
            if not 0 <= age <= settings.SCORECARD_DATA_MAX_AGE_DAYS:
                reason = 'Результаты проверок устарели или датированы будущим числом.'
            elif app.status == 'info' and app.checks.get('source_revision') == source.get('revision'):
                reason = 'После уточнений требуется новый набор проверенных данных.'
    if reason:
        # Do not reuse invalid/stale positive features. The model still evaluates
        # this incomplete snapshot and reports the precise missing factors.
        payload = {'enrichment': {}, 'document': None,
                   'service': {'valuation_date': timezone.localdate().isoformat()}}
    s = app.company_snapshot
    payload['application'] = {
        'amount': str(app.amount), 'term_months': app.months, 'legal_form': s['kind'],
        'name': s['name'], 'signatory': s['representative'], 'address': s['address'],
        'inn': s['inn'], 'ogrn': s['ogrn'], 'purpose': app.purpose,
    }
    app.checks = {
        'simulated': bool(source.get('simulated')), 'source_revision': source.get('revision'),
        'valuation_date': payload['service']['valuation_date'],
        'source_digest': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
    }
    if reason:
        app.checks.update(needs_refresh=True, manual_review_reason=reason)
    return payload
