"""Seed data. Entirely fictional: no real people, insurers or payment rails."""
from datetime import date, datetime
from decimal import Decimal as D

from .models import Coverage, Exclusion
from .security import Actor, Role
from .system import InsuranceSystem

ADMIN = Actor("Ada Admin", Role.ADMIN)
AGENT = Actor("Alex Agent", Role.AGENT)
UNDERWRITER = Actor("Uma Underwriter", Role.UNDERWRITER)
ADJUSTER = Actor("Jo Adjuster", Role.ADJUSTER)
APPROVER = Actor("Pat Approver", Role.APPROVER)
FINANCE = Actor("Fin Finance", Role.FINANCE)


def seed_products(system):
    p = system.products
    health = p.create(ADMIN, "HLT-STD", "Standard Health", "HEALTH", "1200", [
        Coverage("HOSP", "Hospitalisation", D("100000"), D("500"), D("10"), 30),
        Coverage("OPD", "Outpatient", D("5000"), D("50"), D("20"), 0),
        Coverage("DENT", "Dental (optional)", D("2000"), D("0"), D("0"), 90,
                 mandatory=False, additional_premium=D("150")),
    ], [Exclusion("COSMETIC", "Cosmetic procedures", ["cosmetic", "aesthetic"]),
        Exclusion("PREEXIST", "Pre-existing conditions", ["pre-existing"])], (12, 24))
    vehicle = p.create(ADMIN, "VEH-COMP", "Comprehensive Motor", "VEHICLE", "900", [
        Coverage("COLL", "Collision damage", D("40000"), D("500"), D("0"), 0),
        Coverage("THEFT", "Theft", D("30000"), D("1000"), D("0"), 14),
        Coverage("TPL", "Third-party liability", D("300000"), D("0"), D("0"), 0),
    ], [Exclusion("RACING", "Racing or track use", ["racing", "track day"]),
        Exclusion("DUI", "Driving under the influence", ["drunk", "intoxicated", "dui"])], (12,))
    life = p.create(ADMIN, "LIF-TERM", "Term Life", "LIFE", "600", [
        Coverage("DEATH", "Death benefit", D("250000"), D("0"), D("0"), 0),
        Coverage("ADB", "Accidental death rider (optional)", D("100000"), D("0"), D("0"), 0,
                 mandatory=False, additional_premium=D("90")),
    ], [Exclusion("SUICIDE", "Suicide within first year", ["suicide"])], (12, 24, 60))
    return health, vehicle, life


def seed(clock=None) -> InsuranceSystem:
    """Build a populated system: 3 products, 3 customers, policies, claims, payments."""
    now = {"t": datetime(2026, 1, 1, 10, 0)}
    clock = clock or (lambda: now["t"])
    s = InsuranceSystem(clock=clock)
    health, vehicle, life = seed_products(s)
    c = s.customers
    maria = c.create(AGENT, "Maria", "Santos", date(1985, 3, 14), "maria.santos@example.com",
                     "+1 555 0101", "12 Elm Street, Springfield", "NID-1001")
    li = c.create(AGENT, "Li", "Wei", date(1999, 11, 2), "li.wei@example.com",
                  "+1 555 0102", "48 Harbour Road, Shoreham", "NID-1002")
    omar = c.create(AGENT, "Omar", "Haddad", date(1968, 7, 30), "omar.haddad@example.com",
                    "+1 555 0103", "7 Cedar Lane, Rivertown", "NID-1003")
    pol = s.policies
    h = pol.issue(AGENT, maria.id, health.id, 12, date(2026, 1, 1), {"smoker": "no"},
                  selected_optional=["DENT"], discount_codes=["LOYALTY"], frequency="QUARTERLY")
    now["t"] = datetime(2026, 2, 1, 10, 0)
    v = pol.issue(AGENT, li.id, vehicle.id, 12, date(2026, 2, 1),
                  {"vehicle_year": "2022", "usage": "private"}, frequency="MONTHLY")
    # Total limit 350k stays under the 500k approval threshold.
    now["t"] = datetime(2026, 3, 1, 10, 0)
    lf = pol.issue(AGENT, omar.id, life.id, 24, date(2026, 3, 1), {"smoker": "yes"},
                   selected_optional=["ADB"], surcharge_codes=["HIGH_RISK_OCCUPATION"],
                   frequency="ANNUAL")
    now["t"] = datetime(2026, 6, 1, 10, 0)
    pol.issue(AGENT, maria.id, life.id, 12, date(2026, 6, 1), {"smoker": "no"})
    pay = s.payments
    pay.collect_premium(FINANCE, h.id, h.installments[0].amount + h.installments[1].amount)
    pay.collect_premium(FINANCE, v.id, v.installments[0].amount)
    pay.collect_premium(FINANCE, lf.id, lf.installments[0].amount)

    cl = s.claims
    c1 = cl.register(AGENT, h.id, "HOSP", date(2026, 4, 10), "Appendectomy hospital stay",
                     "4500", [("CLAIM_FORM", "form.pdf", b"form"), ("MEDICAL_REPORT", "rep.pdf", b"rep")])
    cl.assess(ADJUSTER, c1.id)
    pay.settle_claim(FINANCE, c1.id, "2000")                       # partial settlement
    c2 = cl.register(AGENT, v.id, "COLL", date(2026, 5, 5), "Rear-ended at junction",
                     "3200", [("CLAIM_FORM", "f.pdf", b"f"), ("POLICE_REPORT", "p.pdf", b"p")])
    cl.assess(ADJUSTER, c2.id)
    pay.settle_claim(FINANCE, c2.id, c2.approved_amount)          # full settlement
    c3 = cl.register(AGENT, h.id, "HOSP", date(2026, 5, 20), "Cosmetic surgery follow-up",
                     "1800", [("CLAIM_FORM", "f.pdf", b"f"), ("MEDICAL_REPORT", "m.pdf", b"m")])
    cl.assess(ADJUSTER, c3.id)                                     # rejected: exclusion
    c4 = cl.register(AGENT, h.id, "HOSP", date(2026, 5, 25), "Emergency surgery and ICU stay",
                     "30000", [("CLAIM_FORM", "f.pdf", b"f"), ("MEDICAL_REPORT", "m.pdf", b"m")])
    cl.assess(ADJUSTER, c4.id)                                     # awaits approver
    cl.register(AGENT, v.id, "THEFT", date(2026, 5, 28), "Bicycle rack stolen", "800")  # no docs yet
    return s
