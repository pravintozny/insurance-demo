from datetime import date, datetime

from insurance import InsuranceSystem
from insurance.mock_data import (ADJUSTER, ADMIN, AGENT, APPROVER, FINANCE, UNDERWRITER,
                                 seed_products)

__all__ = ["make", "ADJUSTER", "ADMIN", "AGENT", "APPROVER", "FINANCE", "UNDERWRITER", "date"]

DOCS = {"HEALTH": [("CLAIM_FORM", "f.pdf", b"f"), ("MEDICAL_REPORT", "m.pdf", b"m")],
        "VEHICLE": [("CLAIM_FORM", "f.pdf", b"f"), ("POLICE_REPORT", "p.pdf", b"p")]}


class World:
    """System at 2026-06-01 with products and customers; clock is adjustable."""

    def __init__(self, config=None, gateway=None):
        self.now = datetime(2026, 6, 1, 10, 0)
        self.s = InsuranceSystem(config=config, clock=lambda: self.now, gateway=gateway)
        self.health, self.vehicle, self.life = seed_products(self.s)
        self.maria = self.s.customers.create(AGENT, "Maria", "Santos", date(1985, 3, 14),
                                             "maria@example.com", "+1 555 0101", "12 Elm St", "N1")
        self.li = self.s.customers.create(AGENT, "Li", "Wei", date(1999, 11, 2),
                                          "li@example.com", "+1 555 0102", "48 Harbour Rd", "N2")

    def health_policy(self, **kw):
        args = dict(customer_id=self.maria.id, product_id=self.health.id, duration_months=12,
                    effective_date=date(2026, 6, 1), risk_profile={"smoker": "no"})
        args.update(kw)
        return self.s.policies.issue(AGENT, **args)

    def claim(self, policy, amount, desc="Hospital stay", coverage="HOSP", loss=date(2026, 6, 1),
              docs=True):
        return self.s.claims.register(AGENT, policy.id, coverage, loss, desc, amount,
                                      DOCS[policy.product_type.value] if docs else ())


def make(**kw):
    return World(**kw)
