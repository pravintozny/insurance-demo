import unittest
from datetime import date, datetime
from decimal import Decimal as D

from insurance.config import RulesConfig
from insurance.errors import PermissionDenied, StateError, ValidationError
from insurance.models import PolicyStatus
from tests.helpers import ADJUSTER, AGENT, APPROVER, UNDERWRITER, make


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.pol = self.w.s.policies

    def test_issue_active_and_number_format(self):
        p = self.w.health_policy()
        self.assertEqual(p.status, PolicyStatus.ACTIVE)
        self.assertEqual(p.number, "HLT-2026-000001")
        self.assertEqual(p.effective_date, date(2026, 6, 1))
        self.assertEqual(p.expiry_date, date(2027, 5, 31))
        self.assertEqual(self.w.health_policy().number, "HLT-2026-000002")

    def test_number_sequences_independent_per_type(self):
        self.w.health_policy()
        v = self.pol.issue(AGENT, self.w.li.id, self.w.vehicle.id, 12, date(2026, 6, 1),
                           {"vehicle_year": "2020", "usage": "private"})
        self.assertEqual(v.number, "VEH-2026-000001")

    def test_future_effective_is_issued_then_rolls(self):
        p = self.w.health_policy(effective_date=date(2026, 7, 1))
        self.assertEqual(p.status, PolicyStatus.ISSUED)
        self.w.now = datetime(2026, 7, 1, 9)
        self.assertEqual(self.pol.refresh_statuses(), [p])
        self.assertEqual(p.status, PolicyStatus.ACTIVE)
        self.w.now = datetime(2027, 7, 1, 9)
        self.pol.refresh_statuses()
        self.assertEqual(p.status, PolicyStatus.EXPIRED)
        self.assertEqual(self.pol.refresh_statuses(), [])

    def test_multi_year_expiry(self):
        p = self.pol.issue(AGENT, self.w.maria.id, self.w.life.id, 60, date(2026, 6, 1),
                           {"smoker": "no"})
        self.assertEqual(p.expiry_date, date(2031, 5, 31))

    def test_coverage_limits_and_selection(self):
        p = self.w.health_policy(selected_optional=["DENT"], coverage_limits={"HOSP": "50000"})
        self.assertEqual(p.coverage("HOSP").limit, D("50000.00"))
        self.assertEqual(p.total_coverage_limit, D("57000.00"))
        self.assertIsNone(self.w.health_policy().coverage("DENT"))

    def test_invalid_coverage_limits(self):
        for limits in ({"HOSP": "999999"}, {"HOSP": "100"}, {"DENT": "1000"}, {"XXX": "1"}):
            with self.assertRaises(ValidationError, msg=limits):
                self.w.health_policy(coverage_limits=limits)

    def test_backdating_blocked_but_configurable(self):
        with self.assertRaises(ValidationError):
            self.w.health_policy(effective_date=date(2026, 5, 30))
        w = make(config=RulesConfig.from_dict({"max_backdate_days": 3}))
        self.assertTrue(w.health_policy(effective_date=date(2026, 5, 30)))

    def test_inactive_customer_blocked(self):
        self.w.s.customers.deactivate(AGENT, self.w.maria.id)
        with self.assertRaises(ValidationError):
            self.w.health_policy()

    def test_approval_workflow_above_threshold(self):
        cfg = RulesConfig.from_dict({"policy_approval_limit": "50000"})
        w = make(config=cfg)
        p = w.health_policy()                       # limit 105k > 50k
        self.assertEqual(p.status, PolicyStatus.PENDING_APPROVAL)
        with self.assertRaises(StateError):         # cannot claim or collect yet
            w.claim(p, "100")
        with self.assertRaises(PermissionDenied):   # wrong role
            w.s.policies.approve(ADJUSTER, p.id)
        # Segregation of duties: an UNDERWRITER who raised it cannot approve it.
        p2 = w.s.policies.issue(UNDERWRITER, w.maria.id, w.health.id, 12, date(2026, 6, 1),
                                {"smoker": "no"})
        with self.assertRaises(PermissionDenied):
            w.s.policies.approve(UNDERWRITER, p2.id)
        w.s.policies.approve(APPROVER, p.id)
        self.assertEqual((p.status, p.approved_by), (PolicyStatus.ACTIVE, APPROVER.name))
        with self.assertRaises(StateError):
            w.s.policies.approve(APPROVER, p.id)

    def test_rejection_requires_reason_and_is_final(self):
        w = make(config=RulesConfig.from_dict({"policy_approval_limit": "50000"}))
        p = w.health_policy()
        with self.assertRaises(ValidationError):
            w.s.policies.reject(APPROVER, p.id, "")
        w.s.policies.reject(APPROVER, p.id, "risk too high")
        self.assertEqual(p.status, PolicyStatus.REJECTED)
        with self.assertRaises(StateError):
            w.s.policies.cancel(UNDERWRITER, p.id, "x")

    def test_cancel(self):
        p = self.w.health_policy()
        with self.assertRaises(PermissionDenied):
            self.pol.cancel(AGENT, p.id, "x")
        with self.assertRaises(ValidationError):
            self.pol.cancel(UNDERWRITER, p.id, "")
        self.pol.cancel(UNDERWRITER, p.id, "customer request")
        self.assertEqual(p.status, PolicyStatus.CANCELLED)
        self.assertEqual([h["to"] for h in p.history], ["ACTIVE", "CANCELLED"])
        with self.assertRaises(StateError):
            self.pol.cancel(UNDERWRITER, p.id, "again")

    def test_audit_trail_for_policy(self):
        p = self.w.health_policy()
        self.pol.cancel(UNDERWRITER, p.id, "r")
        actions = [e.action for e in self.w.s.audit.for_entity("policy", p.id)]
        self.assertEqual(actions, ["policy.issued", "policy.cancelled"])

    def test_audit_chain_detects_tampering(self):
        self.w.health_policy()
        audit = self.w.s.audit
        self.assertTrue(audit.verify())
        audit._entries[0].details["number"] = "HACKED"   # mutate the stored dict
        self.assertFalse(audit.verify())
