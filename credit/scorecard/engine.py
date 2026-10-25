"""Transport validation around the complete supplied ML engine; no local scoring."""
import hashlib
from pathlib import Path
from credit.mlcard.engine import Scorer
from credit.mlcard.models import LoanRequest as ModelRequest
from credit.mlcard.normalization import InputDataError as ModelInputError
from .models import LoanRequest, block, _pick
from .normalization import InputDataError, parse_date
from .policy import load_card


def canonical_payload(payload):
    """Preserve strict input limits/alias checks and bridge both JSON languages.

    The supplied parser detects document branches by Russian keys only, so all
    documents are canonicalized before entering it. No factors are fabricated.
    """
    request = LoanRequest.from_payload(payload)
    f = request.features
    enrichment = block(payload, 'обогащение', 'enrichment')
    registration = parse_date(_pick(enrichment, 'дата_регистрации', 'registration_date'))
    document = None
    if request.branch == 'mature':
        document = {'месяцев_с_поступлениями': f['months_with_inflows'],
                    'средний_приход': f['avg_monthly_inflow'], 'вариативность': f['variability']}
    elif request.branch == 'young':
        document = {'есть_поручительство': f['has_guarantee'], 'доход_поручителя': f['guarantor_income'],
                    'стоимость_залога': f['collateral_value'], 'тип_залога': f['collateral_type']}
    # Only translate explicit English API aliases; Russian free text is passed
    # verbatim to the supplied classifier.
    application = dict(request.application)
    purpose_aliases = {'working capital': 'пополнение оборотных средств',
                       'working_capital': 'пополнение оборотных средств',
                       'equipment': 'оборудование', 'investment': 'инвестиции',
                       'refinancing': 'рефинансирование', 'other': 'прочее'}
    application['purpose'] = purpose_aliases.get(application['purpose'], application['purpose'])
    return {
        'application': dict(application, amount=request.amount, term_months=request.term_months),
        'enrichment': {
            'okved': _pick(enrichment, 'оквэд', 'okved'),
            **{key: f[key] for key in ('address_mass', 'address_unreliable', 'director_disqualified', 'director_mass', 'companies_per_director')},
            'registration_date': registration.isoformat() if registration else None,
            'credit_history': {key: f[key] for key in ('has_history', 'max_overdue_12m', 'active_loans')},
            'ownership': {key: f[key] for key in ('owners_count', 'nominal_owner')},
            'related_parties_count': f['related_parties_count'],
        },
        'document': document,
        'service': {'valuation_date': request.valuation_date.isoformat(), 'card_version': request.card_version},
    }


def score_payload(payload):
    canonical = canonical_payload(payload)
    card = load_card()
    try:
        result = Scorer(card).score(ModelRequest.from_payload(canonical)).to_dict()
    except ModelInputError as exc:
        raise InputDataError(str(exc)) from exc
    result['policy'] = {'max_pd': card.policy.max_pd, 'review_pd': card.policy.review_pd,
                        'source_sha256': hashlib.sha256(Path(card.source_path).read_bytes()).hexdigest()}
    return result
