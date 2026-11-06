import unittest
from datetime import date
from decimal import Decimal as D

from insurance.config import RulesConfig
from insurance.errors import ValidationError
from insurance.models import Frequency
from tests.helpers import make


class PremiumTests(unittest.TestCase):
    def setUp(self):
        self.w = make()

    def quote(self, **kw):
        a = dict(customer_id=self.w.maria.id, product_id=self.w.health.id, duration_months=12,
                 effective_date=date(2026, 6, 1), risk_profile={"smoker": "no"})
        a.update(kw)
        return self.w.s.policies.quote(**a)[0]

    def test_base_only(self):
        b = self.quote()                       # age 41 -> 1.00, non-smoker 1.00, HEALTH tax 5%
        self.assertEqual((b.term_base, b.risk_adjusted, b.net_premium, b.tax_amount, b.total_premium),
                         (D("1200.00"), D("1200.00"), D("1200.00"), D("60.00"), D("1260.00")))

    def test_risk_factors_multiply(self):
        b = self.quote(risk_profile={"smoker": "yes"})      # 1.00 * 1.30
        self.assertEqual(b.risk_multiplier, D("1.3000"))
        self.assertEqual(b.risk_adjusted, D("1560.00"))

    def test_pro_rata_duration(self):
        self.assertEqual(self.quote(duration_months=24).term_base, D("2400.00"))

    def test_optional_coverage_adds_premium(self):
        self.assertEqual(self.quote(selected_optional=["DENT"]).annual_base, D("1350.00"))

    def test_discounts_and_surcharges(self):
        b = self.quote(discount_codes=["LOYALTY", "NO_CLAIM"], surcharge_codes=["PRIOR_CLAIMS"])
        self.assertEqual(b.discount_amount, D("180.00"))     # 15% of 1200
        self.assertEqual(b.surcharge_amount, D("240.00"))    # 20%
        self.assertEqual(b.net_premium, D("1260.00"))
        self.assertEqual(b.total_premium, D("1323.00"))

    def test_discount_cap(self):
        cfg = RulesConfig.from_dict({"max_total_discount_pct": "8"})
        w = make(config=cfg)
        b = w.s.policies.quote(w.maria.id, w.health.id, 12, date(2026, 6, 1), {"smoker": "no"},
                               discount_codes=["LOYALTY", "NO_CLAIM"])[0]
        self.assertEqual(b.discount_pct_applied, D("8"))
        self.assertEqual(b.discount_amount, D("96.00"))

    def test_tax_rates_per_product(self):
        v = self.w.s.policies.quote(self.w.maria.id, self.w.vehicle.id, 12, date(2026, 6, 1),
                                    {"vehicle_year": "2018", "usage": "private"})[0]
        self.assertEqual((v.net_premium, v.tax_amount), (D("900.00"), D("108.00")))
        l = self.w.s.policies.quote(self.w.maria.id, self.w.life.id, 12, date(2026, 6, 1),
                                    {"smoker": "no"})[0]
        self.assertEqual(l.tax_amount, D("0.00"))

    def test_installment_loading_and_schedule(self):
        b = self.quote(frequency="MONTHLY")        # +5% loading = 1260; tax 63; total 1323
        self.assertEqual(b.loading_amount, D("60.00"))
        self.assertEqual(b.total_premium, D("1323.00"))
        self.assertEqual(len(b.installments), 12)
        self.assertEqual(sum(i.amount for i in b.installments), b.total_premium)
        self.assertEqual(b.installments[0].due_date, date(2026, 6, 1))
        self.assertEqual(b.installments[1].due_date, date(2026, 7, 1))

    def test_schedule_remainder_goes_to_last(self):
        from insurance.premium import PremiumCalculator
        s = PremiumCalculator.schedule(D("100.00"), date(2026, 1, 31), 12, Frequency.QUARTERLY)
        self.assertEqual([i.amount for i in s], [D("25.00")] * 4)
        s = PremiumCalculator.schedule(D("100.00"), date(2026, 1, 31), 3, Frequency.MONTHLY)
        self.assertEqual([i.amount for i in s], [D("33.33"), D("33.33"), D("33.34")])
        self.assertEqual([i.due_date for i in s],
                         [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])

    def test_vehicle_derived_factors(self):
        li = self.w.li                       # age 26 -> 25-65 = 1.00; vehicle 2025 -> age 1 -> 1.10
        v = self.w.s.policies.quote(li.id, self.w.vehicle.id, 12, date(2026, 6, 1),
                                    {"vehicle_year": "2025", "usage": "business"})[0]
        self.assertEqual(v.risk_multiplier, D("1.3200"))       # 1.00*1.10*1.20
        self.assertEqual(v.risk_adjusted, D("1188.00"))

    def test_rounding_half_up(self):
        cfg = RulesConfig.from_dict({"installment_loading_pct": {
            "ANNUAL": "0", "SEMIANNUAL": "0", "QUARTERLY": "0.125", "MONTHLY": "0"}})
        w = make(config=cfg)
        b = w.s.policies.quote(w.maria.id, w.health.id, 12, date(2026, 6, 1), {"smoker": "no"},
                               frequency="QUARTERLY")[0]
        self.assertEqual(b.loading_amount, D("1.50"))           # 1200 * 0.125% = 1.5 exactly
        self.assertEqual(b.tax_amount, D("60.08"))              # 1201.5 * 5% = 60.075 -> up

    def test_multi_policy_auto_discount(self):
        self.w.health_policy()
        b = self.quote(product_id=self.w.life.id, risk_profile={"smoker": "no"})
        self.assertEqual([d["code"] for d in b.discounts], ["MULTI_POLICY"])
        self.assertEqual(b.discount_pct_applied, D("7.5"))

    def test_invalid_inputs(self):
        cases = [dict(duration_months=18), dict(risk_profile={"smoker": "maybe"}),
                 dict(risk_profile={}), dict(discount_codes=["NOPE"]),
                 dict(surcharge_codes=["NOPE"]), dict(selected_optional=["HOSP"]),
                 dict(frequency="WEEKLY")]
        for kw in cases:
            with self.assertRaises((ValidationError, ValueError), msg=kw):
                self.quote(**kw)
        with self.assertRaises(ValidationError):    # discount not applicable to product type
            self.quote(product_id=self.w.life.id, risk_profile={"smoker": "no"},
                       discount_codes=["NO_CLAIM"])
        with self.assertRaises(ValidationError):    # vehicle needs vehicle_year
            self.quote(product_id=self.w.vehicle.id, risk_profile={"usage": "private"})

    def test_config_validation(self):
        for bad in ({"tax_rates": {"HEALTH": "150"}}, {"nope": 1},
                    {"discounts": {"X": {"pct": "101", "applies_to": []}}},
                    {"max_total_discount_pct": "-1"},
                    {"min_customer_age": 90}):
            with self.assertRaises(ValidationError, msg=bad):
                RulesConfig.from_dict(bad)
