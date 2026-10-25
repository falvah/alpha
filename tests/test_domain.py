from decimal import Decimal
import unittest
from credit.domain import PRODUCTS, TRANSITIONS, assess, ensure_transition, payment, schedule, validate_identity

class CreditMathTests(unittest.TestCase):
    def test_zero_interest(self):
        self.assertEqual(payment(120000, 12, 0), Decimal("10000.00"))
    def test_known_annuity(self):
        self.assertEqual(payment(100000, 12, 12), Decimal("8884.88"))
    def test_schedule_reconciles_to_cent(self):
        for amount, months, rate in [(100000, 12, 12), (1500000, 24, 24), (30000000, 60, 22.5), (100000, 3, 0)]:
            with self.subTest(amount=amount):
                rows = schedule(amount, months, rate)
                self.assertEqual(rows[-1]["balance"], 0)
                self.assertEqual(sum(row["principal"] for row in rows), Decimal(amount))
                self.assertEqual(sum(row["payment"] for row in rows), Decimal(amount) + sum(row["interest"] for row in rows))
                self.assertTrue(all(row["balance"] >= 0 for row in rows))
    def test_rejects_invalid_terms(self):
        for args in [(0,12,24),(-1,12,24),(100000,0,24),(100000,121,24),(100000,12,-1),('NaN',12,24),('Infinity',12,24),(100000,12,'NaN')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                payment(*args)
    def test_larger_loan_has_larger_payment(self):
        self.assertGreater(payment(2000000,24,24),payment(1000000,24,24))
    def test_longer_term_costs_more_total_interest(self):
        short=sum(r['interest'] for r in schedule(1000000,12,24))
        long=sum(r['interest'] for r in schedule(1000000,36,24))
        self.assertGreater(long,short)

class RiskTests(unittest.TestCase):
    def score(self, **kwargs):
        data=dict(amount=1500000,months=24,rate=24,revenue=2400000,expenses=1600000,existing_payments=0,business_months=36)
        return assess(**(data|kwargs))
    def test_stable_company(self):
        result=self.score()
        self.assertEqual(result['level'],'low')
        self.assertEqual(result['recommendation'],'Рекомендуется одобрение')
        self.assertEqual(result['score'],100)
    def test_insufficient_cash_explained(self):
        result=self.score(expenses=2399000)
        self.assertLess(result['score'],70)
        self.assertTrue(any(f['points']<0 for f in result['factors']))
    def test_registry_block_overrides_score(self):
        self.assertEqual(self.score(registry='inactive')['recommendation'],'Рекомендуется отказ')
    def test_overdue_blocks_recommendation(self):
        self.assertEqual(self.score(overdue=True)['recommendation'],'Рекомендуется отказ')
    def test_other_debt_reduces_score(self):
        self.assertLess(self.score(existing_payments=790000)['score'],self.score()['score'])
    def test_young_company_explanation(self):
        self.assertTrue(any(f['points']==-10 for f in self.score(business_months=5)['factors']))
    def test_deterministic_and_not_a_decision(self):
        self.assertEqual(self.score(),self.score())
        self.assertNotIn('status',self.score())
    def test_score_is_bounded(self):
        result=self.score(expenses=9000000,overdue=True,registry='inactive',business_months=1)
        self.assertEqual(result['score'],0)

class WorkflowTests(unittest.TestCase):
    def test_full_path(self):
        for a,b in zip(['draft','submitted','review','approved'],['submitted','review','approved','funded']):
            ensure_transition(a,b)
    def test_clarification_loop(self):
        for a,b in [('review','info'),('info','submitted'),('submitted','review'),('review','counteroffer'),('counteroffer','funded')]:
            ensure_transition(a,b)
    def test_no_skip_to_disbursement(self):
        for status in ['draft','submitted','review','info','rejected','cancelled']:
            with self.subTest(status=status),self.assertRaises(ValueError):
                ensure_transition(status,'funded')
    def test_terminal_states(self):
        for state in ['funded','rejected','cancelled']:
            self.assertFalse(TRANSITIONS[state])
    def test_identity_lengths(self):
        self.assertTrue(validate_identity('ООО','9900000001','1990000000001'))
        self.assertTrue(validate_identity('ИП','990000000001','199000000000001'))
        self.assertFalse(validate_identity('ИП','9900000001','1990000000001'))
        self.assertFalse(validate_identity('ООО','９９０００００００１','1990000000001'))
    def test_pdf_fixture(self):
        from credit.pdf import make_pdf
        pdf=make_pdf()
        self.assertTrue(pdf.startswith(b'%PDF-'))
        offset=int(pdf.split(b'startxref\n')[-1].split(b'\n')[0])
        self.assertTrue(pdf[offset:].startswith(b'xref'))

if __name__=='__main__': unittest.main()
