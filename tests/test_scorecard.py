import copy
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from credit.scorecard import score_payload, LoanRequest, InputDataError
from credit.scorecard.normalization import parse_decimal, parse_tristate, parse_date, months_between
from tests.support.fake_enrichment import build_enrichment_fake


def example(name="good_mature"):
    return json.loads((Path(__file__).resolve().parents[1] / "fixtures/scorecard" / f"{name}.json").read_text())


class ScorecardTests(unittest.TestCase):
    def test_three_supplied_scenarios(self):
        for name, decision, amount, reason in [("good_mature", "approve", 1500000, "APPROVED"), ("reduced_amount", "approve", 8310000, "APPROVED"), ("mass_address", "decline", None, "ADDRESS_MASS")]:
            with self.subTest(name=name):
                payload = example(name)
                result = score_payload(payload)
                self.assertEqual(result["decision"], decision)
                self.assertEqual(result["approved_amount"], amount)
                self.assertIn(reason, result["reason_codes"])
                self.assertEqual(result, score_payload(payload))
                self.assertGreater(result["pd"], 0)
                self.assertLess(result["pd"], 1)
                self.assertEqual(payload, example(name))

    def test_limit_uses_inflows_not_profit_and_respects_pti(self):
        from credit.domain import payment
        r = score_payload(example("reduced_amount"))
        self.assertLessEqual(payment(r["approved_amount"], 36, r["annual_rate"]), Decimal("900000") * Decimal("0.8") * Decimal("0.45"))
        self.assertGreater(payment(r["approved_amount"] + 10000, 36, r["annual_rate"]), Decimal("900000") * Decimal("0.8") * Decimal("0.45"))

    def test_all_hard_stops_and_thresholds(self):
        for key, value, reason in [("адрес_массовый", "да", "ADDRESS_MASS"), ("адрес_недостоверный", True, "ADDRESS_UNRELIABLE"), ("руководитель_дисквалиф.", 1, "DIRECTOR_DISQUALIFIED"), ("руководитель_массовый", "да", "DIRECTOR_MASS"), ("юрлиц_у_руководителя", 21, "MASS_MANAGEMENT")]:
            with self.subTest(key=key):
                p = example()
                p["обогащение"][key] = value
                r = score_payload(p)
                self.assertEqual(r["decision"], "decline")
                self.assertIn(reason, r["reason_codes"])
                self.assertIsNone(r["monthly_payment"])
        p = example()
        p["обогащение"]["собственность"]["номинальный_владелец"] = True
        self.assertIn("NOMINAL_OWNER", score_payload(p)["reason_codes"])
        p = example()
        p["обогащение"]["кредитная_история"]["макс_просрочка_12м"] = 91
        self.assertIn("CRITICAL_OVERDUE", score_payload(p)["reason_codes"])
        p["обогащение"]["кредитная_история"]["макс_просрочка_12м"] = 90
        self.assertNotIn("CRITICAL_OVERDUE", score_payload(p)["reason_codes"])

    def test_unknown_is_not_negative_check(self):
        p = example()
        p["обогащение"]["адрес_массовый"] = "пусто"
        r = score_payload(p)
        self.assertIn("CRITICAL_DATA_MISSING", r["reason_codes"])
        self.assertLess(r["data_coverage"], 1)
        p["обогащение"]["кредитная_история"]["есть_история"] = None
        self.assertIn("MISSING_REQUIRED_DATA", score_payload(p)["reason_codes"])
        p = example()
        p["обогащение"]["кредитная_история"]["есть_история"] = False
        self.assertNotIn("MISSING_REQUIRED_DATA", score_payload(p)["reason_codes"])

    def test_documents_and_conflicting_branches(self):
        for document in [{}, {"средний_приход": "пусто"}]:
            p = example()
            p["документ"] = document
            self.assertIn("DOCUMENT_MISSING", score_payload(p)["reason_codes"])
        p = example()
        p["документ"]["есть_поручительство"] = False
        with self.assertRaises(InputDataError):
            score_payload(p)
        p = example()
        p["документ"]["месяцев_с_поступлениями"] = 11
        self.assertEqual(score_payload(p)["decision"], "approve")
        self.assertTrue(any(f["id"] == "months_with_inflows" and f["points"] == 0.35 for f in score_payload(p)["factors"]))

    def test_young_annual_income_and_collateral(self):
        p = example()
        p["документ"] = {"есть_поручительство": True, "доход_поручителя": 1200000}
        p["обогащение"]["дата_регистрации"] = "2026-05-22"
        r = score_payload(p)
        self.assertEqual(r["branch"], "young")
        self.assertEqual(r["decision"], "decline")
        self.assertIn("PD_TOO_HIGH", r["reason_codes"])
        p["документ"] = {"есть_поручительство": False, "стоимость_залога": 2000000, "тип_залога": "недвижимость"}
        r = score_payload(p)
        self.assertEqual(r["approved_amount"], 1400000)
        self.assertEqual(r["decision"], "review")
        self.assertNotIn("COLLATERAL_SHORTFALL", r["flags"])

    def test_english_payload_equivalent(self):
        p = {"application": {"amount": "1 500 000 руб.", "term_months": "24", "legal_form": "ООО", "purpose": "working capital"},
             "enrichment": {"okved": "46.90", "address_mass": False, "address_unreliable": 0, "director_disqualified": "нет", "director_mass": False, "companies_per_director": 2, "registration_date": "12.03.2019", "credit_history": {"has_history": True, "max_overdue_12m": 0, "active_loans": 2}, "ownership": {"owners_count": 2, "nominal_owner": False}, "related_parties_count": 3},
             "document": {"months_with_inflows": 24, "avg_monthly_inflow": "900000,00", "variability": "0,22"}, "service": {"valuation_date": "22/10/2026", "card_version": "1.0.0"}}
        self.assertEqual(score_payload(p), score_payload(example()))

    def test_invalid_input_fails_fast(self):
        for amount in [True, "NaN", "Infinity", 0, -1, "oops", "1e1000"]:
            p = example()
            p["заявка"]["сумма"] = amount
            with self.subTest(amount=amount), self.assertRaises(InputDataError):
                score_payload(p)
        for payload in [[], None, {"заявка": []}, {"обогащение": "bad"}]:
            with self.assertRaises(InputDataError):
                score_payload(payload)
        for value in ["неизвестно", 2, {}, []]:
            with self.assertRaises(InputDataError):
                parse_tristate(value)
        p = example()
        p["обогащение"]["дата_регистрации"] = "2027-01-01"
        with self.assertRaises(InputDataError):
            score_payload(p)

    def test_normalization_and_calendar_months(self):
        self.assertEqual(parse_decimal("1\u00a0500,50 ₽"), Decimal("1500.50"))
        self.assertEqual(parse_date("2026-10-22T12:30:00+03:00"), date(2026, 10, 22))
        self.assertEqual(months_between(date(2025, 10, 23), date(2026, 10, 22)), 11)
        self.assertIsNone(parse_tristate("пусто"))
        self.assertIs(parse_tristate("нет"), False)

    def test_fake_enrichment_explicit_and_preserves_unknown(self):
        p = example()
        before = copy.deepcopy(p)
        result = build_enrichment_fake(p, owner_name="Тестовый Владелец")
        self.assertTrue(result["адрес_массовый"])
        self.assertEqual(p, before)
        p["обогащение"].update(result)
        self.assertIn("ADDRESS_MASS", score_payload(p)["reason_codes"])
        empty = build_enrichment_fake({})
        self.assertIsNone(empty["адрес_массовый"])
        self.assertIsNone(empty["собственность"]["номинальный_владелец"])

    def test_version_mismatch_is_visible(self):
        p = example()
        p["служебное"]["версия_карты"] = "old-card"
        r = score_payload(p)
        self.assertEqual(r["card_version"], "1.0.0")
        self.assertIn("CARD_VERSION_MISMATCH", r["flags"])
