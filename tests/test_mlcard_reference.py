"""Parity against the original supplied package, not the previous local model."""
import copy
import importlib
import math
from dataclasses import replace
from pathlib import Path
import sys
import types
import unittest
from credit.scorecard import score_payload
from credit.scorecard.policy import load_card
from credit.mlcard.engine import Scorer
from credit.mlcard.models import LoanRequest
from .test_scorecard import example

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('_original_mlcard')
package.__path__ = [str(ROOT / 'docs' / 'reference' / 'mlcard')]
sys.modules[package.__name__] = package
original_engine = importlib.import_module('_original_mlcard.engine')
original_models = importlib.import_module('_original_mlcard.models')
original_config = importlib.import_module('_original_mlcard.config')


class SuppliedModelTests(unittest.TestCase):
    def test_all_supplied_files_are_preserved_byte_for_byte(self):
        for source in (ROOT / 'docs' / 'reference' / 'mlcard').iterdir():
            if source.suffix in {'.py', '.yaml'}:
                with self.subTest(file=source.name):
                    self.assertEqual(source.read_bytes(), (ROOT / 'credit' / 'mlcard' / source.name).read_bytes())

    def test_adapter_matches_original_model_for_all_branches_and_stops(self):
        original = original_config.CardConfig.load(ROOT / 'docs/reference/mlcard/card.yaml')
        original = replace(original, policy=replace(original.policy, review_pd=0.20))
        cases = [example(name) for name in ['good_mature', 'reduced_amount', 'mass_address']]
        for overdue in [0, 30, 60, 90, 91]:
            p = example()
            p['обогащение']['кредитная_история'].update({'есть_история': False, 'макс_просрочка_12м': overdue, 'активных_кредитов': 6})
            cases.append(p)
        for value in [None, True, False]:
            p = example()
            p['обогащение']['адрес_недостоверный'] = value
            cases.append(p)
        for guarantee, income, collateral in [(True, 1200000, None), (True, 6000000, 3000000), (False, None, 2000000), (False, None, 0)]:
            p = example()
            p['обогащение']['дата_регистрации'] = '2026-05-22'
            p['документ'] = {'есть_поручительство': guarantee, 'доход_поручителя': income,
                'стоимость_залога': collateral, 'тип_залога': 'недвижимость' if collateral else None}
            cases.append(p)
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                expected = original_engine.Scorer(original).score(original_models.LoanRequest.from_payload(payload)).to_dict()
                actual = score_payload(payload)
                actual.pop('policy')
                self.assertEqual(actual, expected)

    def test_pd_cutoffs_use_unrounded_probability(self):
        request = LoanRequest.from_payload(example())
        card = load_card()
        base = Scorer(card).score(request)
        for pd, decision in [(0.119999, 'approve'), (0.120001, 'review'), (0.199999, 'review'), (0.200001, 'decline')]:
            with self.subTest(pd=pd):
                intercept = math.log(pd / (1 - pd)) - base.score_points
                result = Scorer(replace(card, intercept=intercept)).score(request)
                self.assertEqual(result.decision, decision)

    def test_young_limit_is_minimum_of_income_and_collateral_not_maximum(self):
        p = example()
        p['обогащение']['дата_регистрации'] = '2026-05-22'
        p['документ'] = {'есть_поручительство': True, 'доход_поручителя': 6000000,
                         'стоимость_залога': 3000000, 'тип_залога': 'недвижимость'}
        p['заявка']['сумма'] = 2500000
        r = score_payload(p)
        self.assertEqual(r['decision'], 'approve')
        self.assertEqual(r['approved_amount'], 2100000)
        self.assertEqual(r['limit_amount'], 2100000)
        self.assertIn('AMOUNT_REDUCED', r['flags'])
