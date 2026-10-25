"""Explicit synthetic inputs for seed_workspace, never a live enrichment fallback."""
import calendar
from django.utils import timezone
from .scorecard_workflow import load_case
from .scoring_sources import store_source


def seed_company_source(company, *, grey=False):
    payload = load_case('good_mature')
    today = timezone.localdate()
    month_index = today.year * 12 + today.month - 1 - company.business_months
    year, month = divmod(month_index, 12)
    registration = today.replace(year=year, month=month + 1,
        day=min(today.day, calendar.monthrange(year, month + 1)[1]))
    payload['заявка'].update({'инн': company.inn, 'огрн': company.ogrn})
    payload['служебное'].update({'дата_оценки': today.isoformat()})
    e = payload['обогащение']
    e['дата_регистрации'] = registration.isoformat()
    if company.mock_overdue:
        e['кредитная_история']['макс_просрочка_12м'] = 91
    if grey:
        e['кредитная_история'].update({'есть_история': False, 'макс_просрочка_12м': 60, 'активных_кредитов': 6})
    if company.business_months >= 12:
        payload['документ'].update({'месяцев_с_поступлениями': min(24, company.business_months),
                                   'средний_приход': str(company.revenue)})
    else:
        payload['документ'] = {'есть_поручительство': True, 'доход_поручителя': 1200000,
                               'стоимость_залога': 2000000, 'тип_залога': 'недвижимость'}
    store_source(company, payload, simulated=True)
