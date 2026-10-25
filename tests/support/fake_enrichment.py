"""Explicit opt-in simulation. Never overwrite an existing enrichment response."""
from .fake_enrichment_data import MASS_ADDRESSES, DISQUALIFIED_DIRECTORS, MASS_DIRECTORS, NOMINAL_OWNERS
from credit.scorecard.normalization import normalize_text
from credit.scorecard.models import block, _pick


def check_list(value, values):
    return None if not value or not value.strip() else normalize_text(value) in {normalize_text(item) for item in values}


def check_address_mass(address):
    return check_list(address, MASS_ADDRESSES)


def check_address_unreliable(address):
    return "фейковый" in normalize_text(address) if address and address.strip() else None


def check_director_disqualified(name):
    return check_list(name, DISQUALIFIED_DIRECTORS)


def check_director_mass(name):
    return check_list(name, MASS_DIRECTORS)


def check_companies_per_director(name):
    result = check_director_mass(name)
    return None if result is None else 25 if result else 2


def check_nominal_owner(name):
    return check_list(name, NOMINAL_OWNERS)


def build_enrichment_fake(payload, owner_name=None):
    app = block(payload, "заявка", "application") if "заявка" in payload or "application" in payload else payload
    address, director = _pick(app, "адрес", "address"), _pick(app, "в_лице", "signatory")
    return {
        "адрес_массовый": check_address_mass(address), "адрес_недостоверный": check_address_unreliable(address),
        "руководитель_дисквалиф.": check_director_disqualified(director), "руководитель_массовый": check_director_mass(director),
        "юрлиц_у_руководителя": check_companies_per_director(director),
        "собственность": {"номинальный_владелец": check_nominal_owner(owner_name)},
    }
