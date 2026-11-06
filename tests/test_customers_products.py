import unittest
from datetime import date
from decimal import Decimal as D

from insurance.errors import PermissionDenied, ValidationError
from insurance.models import Coverage
from tests.helpers import ADMIN, AGENT, ADJUSTER, make


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.c = self.w.s.customers

    def create(self, **kw):
        a = dict(first_name="Ann", last_name="Lee", date_of_birth=date(1990, 1, 1),
                 email="ann@example.com", phone="+44 20 7946 0958", address="1 Road",
                 national_id="X1")
        a.update(kw)
        return self.c.create(AGENT, **a)

    def test_create_and_audit(self):
        cust = self.create()
        self.assertTrue(cust.id.startswith("CUS-"))
        self.assertEqual(self.w.s.audit.for_entity("customer", cust.id)[0].action, "customer.created")

    def test_validations(self):
        for kw in ({"email": "bad"}, {"phone": "abc"}, {"first_name": " "},
                   {"date_of_birth": date(2015, 1, 1)}, {"date_of_birth": date(2030, 1, 1)},
                   {"date_of_birth": date(1900, 1, 1)}, {"address": ""}, {"national_id": ""}):
            with self.assertRaises(ValidationError, msg=kw):
                self.create(**kw)

    def test_duplicates_rejected(self):
        self.create()
        with self.assertRaises(ValidationError):
            self.create(email="other@example.com")      # same national id
        with self.assertRaises(ValidationError):
            self.create(national_id="X2")               # same email

    def test_update_contact_records_changes(self):
        cust = self.create()
        self.c.update_contact(AGENT, cust.id, phone="+1 555 9999")
        self.assertEqual(cust.phone, "+1 555 9999")
        e = self.w.s.audit.for_entity("customer", cust.id)[-1]
        self.assertEqual(e.details["phone"], ["+44 20 7946 0958", "+1 555 9999"])
        with self.assertRaises(ValidationError):
            self.c.update_contact(AGENT, cust.id, email="nope")

    def test_role_enforced(self):
        with self.assertRaises(PermissionDenied):
            self.c.create(ADJUSTER, "A", "B", date(1990, 1, 1), "a@b.co", "+1 555 0000", "x", "Z")

    def test_policy_ownership(self):
        p = self.w.health_policy()
        self.assertEqual(self.c.policies_of(self.w.maria.id), [p])
        self.assertEqual(self.c.policies_of(self.w.li.id), [])


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.w = make()
        self.p = self.w.s.products

    def cov(self, **kw):
        a = dict(code="C", name="C", limit=D("1000"))
        a.update(kw)
        return Coverage(**a)

    def create(self, coverages=None, **kw):
        a = dict(code="NEW", name="New", ptype="HEALTH", base_annual_premium="100",
                 coverages=[self.cov()] if coverages is None else coverages)
        a.update(kw)
        return self.p.create(ADMIN, **a)

    def test_seeded_types(self):
        self.assertEqual({x.type.value for x in self.p.list()}, {"HEALTH", "VEHICLE", "LIFE"})

    def test_create_valid(self):
        self.assertEqual(self.create().base_annual_premium, D("100.00"))

    def test_invalid_configs(self):
        bad = [dict(base_annual_premium="0"), dict(coverages=[]), dict(code="HLT-STD"),
               dict(coverages=[self.cov(deductible=D("1000"))]),
               dict(coverages=[self.cov(copay_pct=D("101"))]),
               dict(coverages=[self.cov(), self.cov()]),
               dict(coverages=[self.cov(mandatory=False, additional_premium=D("10"))]),
               dict(coverages=[self.cov(), self.cov(code="O", mandatory=False)]),
               dict(allowed_durations_months=[])]
        for kw in bad:
            with self.assertRaises(ValidationError, msg=kw):
                self.create(**kw)

    def test_admin_only_and_premium_change_audited(self):
        with self.assertRaises(PermissionDenied):
            self.p.create(AGENT, "X", "X", "HEALTH", "100", [self.cov()])
        self.p.set_base_premium(ADMIN, self.w.health.id, "1300")
        e = self.w.s.audit.for_entity("product", self.w.health.id)[-1]
        self.assertEqual((e.details["old"], e.details["new"]), ("1200.00", "1300.00"))

    def test_retired_product_cannot_be_sold(self):
        self.p.set_active(ADMIN, self.w.health.id, False)
        with self.assertRaises(ValidationError):
            self.w.health_policy()
        self.assertNotIn(self.w.health, self.p.list())
