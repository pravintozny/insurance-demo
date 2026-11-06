import unittest
from datetime import date
from decimal import Decimal as D

from insurance.config import RulesConfig
from insurance.errors import PermissionDenied, StateError, ValidationError
from insurance.models import ClaimStatus
from tests.helpers import ADJUSTER, ADMIN, AGENT, APPROVER, FINANCE, UNDERWRITER, make


class ClaimRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.p = self.w.health_policy()
        self.cl = self.w.s.claims

    def reg(self, **kw):
        a = dict(policy_id=self.p.id, coverage_code="HOSP", loss_date=date(2026, 6, 1),
                 description="Stay", claimed_amount="1000")
        a.update(kw)
        return self.cl.register(AGENT, **a)

    def test_register(self):
        c = self.reg()
        self.assertEqual((c.status, c.number, c.policy_id), (ClaimStatus.REGISTERED, "CLM-000001", self.p.id))
        self.assertEqual(c.customer_id, self.w.maria.id)
        self.assertEqual(c.claimed_amount, D("1000.00"))

    def test_validations(self):
        for kw in (dict(coverage_code="DENT"), dict(loss_date=date(2026, 5, 31)),
                   dict(loss_date=date(2027, 6, 1)), dict(claimed_amount="0"),
                   dict(claimed_amount="-5"), dict(description="  ")):
            with self.assertRaises(ValidationError, msg=kw):
                self.reg(**kw)

    def test_reporting_window(self):
        self.w.now = self.w.now.replace(year=2026, month=12, day=15)
        with self.assertRaises(ValidationError):
            self.reg(loss_date=date(2026, 6, 1))
        self.assertTrue(self.reg(loss_date=date(2026, 12, 1)))

    def test_cancelled_policy_blocked(self):
        self.w.s.policies.cancel(UNDERWRITER, self.p.id, "r")
        with self.assertRaises(StateError):
            self.reg()

    def test_documents(self):
        c = self.reg()
        d = self.cl.add_document(AGENT, c.id, "INVOICE", "inv.pdf", b"abc")
        self.assertEqual(len(d.sha256), 64)
        with self.assertRaises(ValidationError):
            self.cl.add_document(AGENT, c.id, "SELFIE", "x.png")
        self.assertEqual(self.cl.missing_documents(c), ["CLAIM_FORM", "MEDICAL_REPORT"])

    def test_assess_blocked_until_documents_present(self):
        self.w.now = self.w.now.replace(month=8)
        c = self.reg(loss_date=date(2026, 7, 15))
        with self.assertRaises(StateError):
            self.cl.assess(ADJUSTER, c.id)
        self.cl.add_document(AGENT, c.id, "CLAIM_FORM", "f.pdf")
        self.cl.add_document(AGENT, c.id, "MEDICAL_REPORT", "m.pdf")
        self.assertEqual(self.cl.assess(ADJUSTER, c.id).status, ClaimStatus.APPROVED)

    def test_roles(self):
        with self.assertRaises(PermissionDenied):
            self.cl.register(FINANCE, self.p.id, "HOSP", date(2026, 6, 1), "x", "1")
        c = self.w.claim(self.p, "1000")
        with self.assertRaises(PermissionDenied):
            self.cl.assess(AGENT, c.id)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.p = self.w.health_policy()      # HOSP: ded 500, copay 10%, waiting 30d, limit 100k
        self.cl = self.w.s.claims

    def assess(self, amount, desc="Hospital stay", coverage="HOSP", loss=date(2026, 7, 15)):
        self.w.now = self.w.now.replace(month=7, day=20)
        c = self.w.claim(self.p, amount, desc, coverage, loss)
        return self.cl.assess(ADJUSTER, c.id)

    def test_deductible_and_copay(self):
        c = self.assess("4500")               # (4500-500)=4000; copay 400; payable 3600
        a = c.assessment
        self.assertEqual((a.deductible, a.copay, a.approved_amount, a.rejected_amount),
                         (D("500.00"), D("400.00"), D("3600.00"), D("900.00")))
        self.assertTrue(a.coverage_valid)
        self.assertEqual(c.status, ClaimStatus.APPROVED)
        self.assertEqual(c.approved_amount + c.rejected_amount, c.claimed_amount)

    def test_claim_below_deductible_rejected(self):
        c = self.assess("400")
        self.assertEqual((c.status, c.approved_amount, c.rejected_amount),
                         (ClaimStatus.REJECTED, D("0.00"), D("400.00")))

    def test_exclusion_rejects(self):
        c = self.assess("2000", desc="Cosmetic surgery")
        self.assertEqual(c.status, ClaimStatus.REJECTED)
        self.assertEqual(c.assessment.exclusion_codes, ["COSMETIC"])
        self.assertEqual(c.rejected_amount, D("2000.00"))

    def test_exclusion_case_insensitive(self):
        c = self.assess("2000", desc="Treatment of PRE-EXISTING condition")
        self.assertEqual(c.assessment.exclusion_codes, ["PREEXIST"])

    def test_waiting_period(self):
        self.w.now = self.w.now.replace(month=7, day=20)
        c = self.w.claim(self.p, "2000", loss=date(2026, 6, 20))   # inside 30-day waiting period
        c = self.cl.assess(ADJUSTER, c.id)
        self.assertFalse(c.assessment.coverage_valid)
        self.assertEqual(c.status, ClaimStatus.REJECTED)
        # Exactly day 30 after effective date is covered.
        c2 = self.cl.assess(ADJUSTER, self.w.claim(self.p, "2000", loss=date(2026, 7, 1)).id)
        self.assertEqual(c2.status, ClaimStatus.APPROVED)

    def test_loss_after_backdated_cancellation_rejected(self):
        self.w.now = self.w.now.replace(month=8)
        late = self.w.claim(self.p, "1000", loss=date(2026, 7, 20))
        early = self.w.claim(self.p, "1000", loss=date(2026, 7, 5))
        self.w.s.policies.cancel(UNDERWRITER, self.p.id, "r", on=date(2026, 7, 10))
        self.assertEqual(self.cl.assess(ADJUSTER, late.id).status, ClaimStatus.REJECTED)
        self.assertIn("loss occurred after policy cancellation", late.assessment.reasons)
        self.assertEqual(self.cl.assess(ADJUSTER, early.id).status, ClaimStatus.APPROVED)

    def test_limit_cap_across_claims(self):
        w = make()
        p = w.health_policy(coverage_limits={"HOSP": "6000"})
        w.now = w.now.replace(month=8)
        cl = w.s.claims
        c1 = cl.assess(ADJUSTER, w.claim(p, "5500", loss=date(2026, 7, 15)).id)  # payable 4500
        self.assertEqual(c1.approved_amount, D("4500.00"))
        c2 = cl.assess(ADJUSTER, w.claim(p, "5500", loss=date(2026, 7, 16)).id)  # 1500 left
        self.assertEqual(c2.approved_amount, D("1500.00"))
        self.assertEqual(c2.rejected_amount, D("4000.00"))
        self.assertIn("capped by remaining coverage limit 1500.00", c2.assessment.reasons)
        c3 = cl.assess(ADJUSTER, w.claim(p, "1000", loss=date(2026, 7, 17)).id)  # exhausted
        self.assertEqual(c3.status, ClaimStatus.REJECTED)

    def test_rejected_claims_do_not_consume_limit(self):
        w = make()
        p = w.health_policy(coverage_limits={"HOSP": "6000"})
        w.now = w.now.replace(month=8)
        w.s.claims.assess(ADJUSTER, w.claim(p, "2000", "Cosmetic", loss=date(2026, 7, 15)).id)
        c = w.s.claims.assess(ADJUSTER, w.claim(p, "5500", loss=date(2026, 7, 16)).id)
        self.assertEqual(c.approved_amount, D("4500.00"))

    def test_assessment_steps_recorded(self):
        c = self.assess("4500")
        self.assertTrue(any("co-pay 10%" in s for s in c.assessment.steps))

    def test_cannot_assess_twice(self):
        c = self.assess("4500")
        with self.assertRaises(StateError):
            self.cl.assess(ADJUSTER, c.id)


class ApprovalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.p = self.w.health_policy()
        self.w.now = self.w.now.replace(month=8)
        self.cl = self.w.s.claims
        self.c = self.cl.assess(ADJUSTER, self.w.claim(self.p, "30000", loss=date(2026, 7, 15)).id)

    def test_large_claim_pends(self):
        self.assertEqual(self.c.status, ClaimStatus.PENDING_APPROVAL)
        self.assertEqual(self.c.approved_amount, D("26550.00"))   # (30000-500)*0.9
        with self.assertRaises(StateError):                        # cannot settle yet
            self.w.s.payments.settle_claim(FINANCE, self.c.id, "100")

    def test_approve(self):
        self.cl.approve(APPROVER, self.c.id, "ok")
        self.assertEqual((self.c.status, self.c.decision_by), (ClaimStatus.APPROVED, APPROVER.name))
        with self.assertRaises(StateError):
            self.cl.approve(APPROVER, self.c.id)

    def test_wrong_role_denied(self):
        for actor in (ADJUSTER, FINANCE, AGENT):
            with self.assertRaises(PermissionDenied):
                self.cl.approve(actor, self.c.id)

    def test_segregation_of_duties(self):
        # ADMIN passes role checks, so SoD is what must stop the assessor approving own work.
        other = self.cl.assess(ADMIN, self.w.claim(self.p, "30000", loss=date(2026, 7, 16)).id)
        with self.assertRaises(PermissionDenied):
            self.cl.approve(ADMIN, other.id)

    def test_reviewer_rejection(self):
        with self.assertRaises(ValidationError):
            self.cl.reject(APPROVER, self.c.id, "")
        self.cl.reject(APPROVER, self.c.id, "suspected fraud")
        self.assertEqual((self.c.status, self.c.approved_amount, self.c.rejected_amount),
                         (ClaimStatus.REJECTED, D("0.00"), D("30000.00")))

    def test_threshold_is_configurable(self):
        w = make(config=RulesConfig.from_dict({"claim_auto_approval_limit": "100000"}))
        p = w.health_policy()
        w.now = w.now.replace(month=8)
        c = w.s.claims.assess(ADJUSTER, w.claim(p, "30000", loss=date(2026, 7, 15)).id)
        self.assertEqual(c.status, ClaimStatus.APPROVED)

    def test_history_and_audit(self):
        self.cl.approve(APPROVER, self.c.id)
        self.assertEqual([h["to"] for h in self.c.history],
                         ["REGISTERED", "PENDING_APPROVAL", "APPROVED"])
        self.assertEqual([e.action for e in self.w.s.audit.for_entity("claim", self.c.id)],
                         ["claim.registered", "claim.document_added", "claim.document_added",
                          "claim.assessed", "claim.approved"])
