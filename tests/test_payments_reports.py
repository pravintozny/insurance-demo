import unittest
from datetime import date
from decimal import Decimal as D

from insurance.errors import PermissionDenied, StateError, ValidationError
from insurance.mock_data import seed
from insurance.models import ClaimStatus, SettlementStatus
from insurance.payments import MockPaymentGateway
from tests.helpers import ADJUSTER, AGENT, APPROVER, FINANCE, UNDERWRITER, make


class PremiumPaymentTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.p = self.w.health_policy(frequency="QUARTERLY")   # 1200*1.03*1.05 = 1297.80 / 4
        self.pay = self.w.s.payments

    def test_allocation_oldest_first(self):
        self.assertEqual(self.p.installments[0].amount, D("324.45"))
        r = self.pay.collect_premium(FINANCE, self.p.id, "400")
        self.assertEqual([(a["installment"], a["amount"]) for a in r.allocations],
                         [(1, D("324.45")), (2, D("75.55"))])
        self.assertEqual([i.status.value for i in self.p.installments[:3]], ["PAID", "PARTIAL", "PENDING"])
        self.assertEqual(self.pay.outstanding_premium(self.p.id), D("897.80"))

    def test_overpayment_and_bad_amounts(self):
        for amt in ("0", "-1", "99999"):
            with self.assertRaises(ValidationError):
                self.pay.collect_premium(FINANCE, self.p.id, amt)

    def test_role(self):
        with self.assertRaises(PermissionDenied):
            self.pay.collect_premium(ADJUSTER, self.p.id, "10")

    def test_cancelled_policy_blocked(self):
        self.w.s.policies.cancel(UNDERWRITER, self.p.id, "r")
        with self.assertRaises(StateError):
            self.pay.collect_premium(FINANCE, self.p.id, "10")


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.gw = MockPaymentGateway(fail_references={"BAD"})
        self.w = make(gateway=self.gw)
        self.p = self.w.health_policy()
        self.w.now = self.w.now.replace(month=8)
        self.c = self.w.s.claims.assess(ADJUSTER, self.w.claim(self.p, "4500", loss=date(2026, 7, 15)).id)
        self.pay = self.w.s.payments                     # approved 3600.00

    def test_partial_then_full(self):
        s1 = self.pay.settle_claim(FINANCE, self.c.id, "1000")
        self.assertEqual((s1.status, self.c.status, self.c.paid_amount),
                         (SettlementStatus.COMPLETED, ClaimStatus.PARTIALLY_SETTLED, D("1000.00")))
        self.pay.settle_claim(FINANCE, self.c.id, "2600")
        self.assertEqual((self.c.status, self.c.outstanding_amount), (ClaimStatus.SETTLED, D("0.00")))
        self.assertEqual([s.amount for s in self.pay.settlement_history(self.c.id)],
                         [D("1000.00"), D("2600.00")])

    def test_overpay_rejected(self):
        self.pay.settle_claim(FINANCE, self.c.id, "3000")
        with self.assertRaises(ValidationError):
            self.pay.settle_claim(FINANCE, self.c.id, "600.01")

    def test_cannot_settle_settled_or_unapproved(self):
        self.pay.settle_claim(FINANCE, self.c.id, "3600")
        with self.assertRaises(StateError):
            self.pay.settle_claim(FINANCE, self.c.id, "1")
        rej = self.w.s.claims.assess(ADJUSTER, self.w.claim(self.p, "100", loss=date(2026, 7, 16)).id)
        with self.assertRaises(StateError):
            self.pay.settle_claim(FINANCE, rej.id, "1")

    def test_failed_payment_recorded_but_not_applied(self):
        s = self.pay.settle_claim(FINANCE, self.c.id, "1000", reference="BAD")
        self.assertEqual(s.status, SettlementStatus.FAILED)
        self.assertEqual((self.c.paid_amount, self.c.status), (D("0.00"), ClaimStatus.APPROVED))
        self.assertEqual(len(self.pay.settlement_history(self.c.id)), 1)
        self.pay.settle_claim(FINANCE, self.c.id, "1000")        # retry succeeds
        self.assertEqual(self.c.paid_amount, D("1000.00"))

    def test_invalid_amount_and_role(self):
        with self.assertRaises(ValidationError):
            self.pay.settle_claim(FINANCE, self.c.id, "0")
        with self.assertRaises(PermissionDenied):
            self.pay.settle_claim(ADJUSTER, self.c.id, "10")

    def test_settlement_audited(self):
        self.pay.settle_claim(FINANCE, self.c.id, "1000", reference="BAD")
        self.pay.settle_claim(FINANCE, self.c.id, "1000")
        self.assertEqual([e.action for e in self.w.s.audit.for_entity("claim", self.c.id)][-2:],
                         ["claim.settlement_failed", "claim.settlement_completed"])


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.s = seed()
        self.r = self.s.reports

    def test_active_policies(self):
        a = self.r.active_policies()
        self.assertEqual(a["count"], 4)
        self.assertEqual(a["by_product_type"]["LIFE"]["count"], 2)
        self.assertEqual(a["total_premium"], sum(p.premium.total_premium for p in self.s.ctx.policies.values()))

    def test_premium_collection(self):
        pc = self.r.premium_collection()
        self.assertEqual(pc["collected"], D("2686.10"))
        self.assertEqual(pc["billed"] - pc["collected"], pc["outstanding"])
        self.assertLessEqual(pc["overdue"], pc["due_to_date"])

    def test_claims_by_status(self):
        c = self.r.claims_by_status()
        self.assertEqual(c["REGISTERED"], 1)
        self.assertEqual(c["PENDING_APPROVAL"], 1)
        self.assertEqual(c["REJECTED"], 1)
        self.assertEqual(sum(c.values()), len(self.s.ctx.claims))

    def test_settlement_amounts(self):
        st = self.r.settlement_amounts()
        self.assertEqual((st["approved_total"], st["paid_total"], st["outstanding"]),
                         (D("6300.00"), D("4700.00"), D("1600.00")))

    def test_approval_ratio(self):
        r = self.r.claim_approval_ratio()
        self.assertEqual((r["approved_count"], r["rejected_count"]), (2, 1))
        self.assertEqual(r["count_ratio"], D("0.6667"))
        self.assertEqual(r["amount_ratio"], D("0.6632"))     # 6300 / 9500

    def test_empty_system_ratios_are_none(self):
        w = make()
        self.assertIsNone(w.s.reports.claim_approval_ratio()["count_ratio"])
        self.assertIsNone(w.s.reports.premium_collection()["collection_rate_on_due"])

    def test_pending_cancelled_excluded(self):
        n = self.r.active_policies()["count"]
        pol = next(p for p in self.s.ctx.policies.values())
        self.s.policies.cancel(UNDERWRITER, pol.id, "r")
        self.assertEqual(self.r.active_policies()["count"], n - 1)

    def test_audit_trail_intact_and_summary_runs(self):
        self.assertTrue(self.s.audit.verify())
        self.assertEqual(set(self.r.summary()), {"active_policies", "premium_collection",
                         "claims_by_status", "settlement_amounts", "claim_approval_ratio"})
