"""Configurable business rules. Every number used by premium calculation,
assessment and approval workflows lives here, not in code.

Override any top-level key via RulesConfig.from_dict({...}) or from_json(path).
All percentages are expressed in percent (5 = 5%); multipliers are plain factors.
"""
import copy
import json
from dataclasses import dataclass, field
from decimal import Decimal

from .errors import ValidationError
from .util import D

DEFAULTS = {
    "currency": "USD",
    # Tax (e.g. VAT / premium tax) per product type, percent of net premium.
    "tax_rates": {"HEALTH": "5", "VEHICLE": "12", "LIFE": "0"},
    # Risk factor tables: product type -> factor -> value -> multiplier.
    # Multipliers of all factors are multiplied together.
    "risk_factors": {
        "HEALTH": {
            "age_band": {"18-30": "0.90", "31-45": "1.00", "46-60": "1.35", "61+": "1.80"},
            "smoker": {"no": "1.00", "yes": "1.30"},
        },
        "VEHICLE": {
            "age_band": {"18-24": "1.40", "25-65": "1.00", "66+": "1.15"},
            "vehicle_age_band": {"0-3": "1.10", "4-10": "1.00", "11+": "0.90"},
            "usage": {"private": "1.00", "business": "1.20"},
        },
        "LIFE": {
            "age_band": {"18-30": "0.80", "31-45": "1.00", "46-60": "1.60", "61+": "2.50"},
            "smoker": {"no": "1.00", "yes": "1.50"},
        },
    },
    # Bands used to derive age_band / vehicle_age_band: [inclusive max, label]; None = open ended.
    "age_bands": {
        "HEALTH": [[30, "18-30"], [45, "31-45"], [60, "46-60"], [None, "61+"]],
        "VEHICLE": [[24, "18-24"], [65, "25-65"], [None, "66+"]],
        "LIFE": [[30, "18-30"], [45, "31-45"], [60, "46-60"], [None, "61+"]],
    },
    "vehicle_age_bands": [[3, "0-3"], [10, "4-10"], [None, "11+"]],
    # Discounts / surcharges: code -> percent (+ product types it applies to).
    "discounts": {
        "NO_CLAIM": {"pct": "10", "applies_to": ["VEHICLE", "HEALTH"]},
        "LOYALTY": {"pct": "5", "applies_to": ["HEALTH", "VEHICLE", "LIFE"]},
        "MULTI_POLICY": {"pct": "7.5", "applies_to": ["HEALTH", "VEHICLE", "LIFE"]},
    },
    "surcharges": {
        "HIGH_RISK_OCCUPATION": {"pct": "15", "applies_to": ["HEALTH", "LIFE"]},
        "PRIOR_CLAIMS": {"pct": "20", "applies_to": ["VEHICLE", "HEALTH"]},
    },
    # Discounts granted automatically when the customer already holds an active policy.
    "auto_multi_policy_discount": "MULTI_POLICY",
    "max_total_discount_pct": "25",
    "max_total_surcharge_pct": "50",
    # Loading (percent of premium before tax) for paying in instalments.
    "installment_loading_pct": {"ANNUAL": "0", "SEMIANNUAL": "2", "QUARTERLY": "3", "MONTHLY": "5"},
    # Customers.
    "min_customer_age": 18,
    "max_customer_age": 80,
    # Policy workflow: total coverage limit above this needs a second-person approval.
    "policy_approval_limit": "500000",
    "max_backdate_days": 0,
    # Claims.
    "claim_reporting_window_days": 90,
    "required_claim_documents": {
        "HEALTH": ["CLAIM_FORM", "MEDICAL_REPORT"],
        "VEHICLE": ["CLAIM_FORM", "POLICE_REPORT"],
        "LIFE": ["CLAIM_FORM", "DEATH_CERTIFICATE"],
    },
    # Claims with an approved amount up to this are auto-approved after assessment;
    # above it a different person with the APPROVER role must approve.
    "claim_auto_approval_limit": "10000",
}


@dataclass
class RulesConfig:
    data: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS))

    @classmethod
    def from_dict(cls, overrides=None):
        data = copy.deepcopy(DEFAULTS)
        for key, value in (overrides or {}).items():
            if key not in DEFAULTS:
                raise ValidationError(f"unknown config key: {key}")
            data[key] = value
        cfg = cls(data)
        cfg.validate()
        return cfg

    @classmethod
    def from_json(cls, path):
        with open(path) as fh:
            return cls.from_dict(json.load(fh))

    def __getitem__(self, key):
        return self.data[key]

    def pct(self, *path) -> Decimal:
        node = self.data
        for p in path:
            node = node[p]
        return D(node)

    def validate(self):
        for key in ("max_total_discount_pct", "max_total_surcharge_pct",
                    "policy_approval_limit", "claim_auto_approval_limit"):
            if D(self.data[key]) < 0:
                raise ValidationError(f"{key} must be >= 0")
        for group in ("tax_rates", "installment_loading_pct"):
            for k, v in self.data[group].items():
                if D(v) < 0 or (group == "tax_rates" and D(v) > 100):
                    raise ValidationError(f"{group}[{k}] out of range")
        for group in ("discounts", "surcharges"):
            for code, rule in self.data[group].items():
                if not (0 <= D(rule["pct"]) <= 100):
                    raise ValidationError(f"{group}[{code}].pct out of range")
        for ptype, factors in self.data["risk_factors"].items():
            for fname, table in factors.items():
                for val, mult in table.items():
                    if D(mult) <= 0:
                        raise ValidationError(f"risk factor {ptype}.{fname}.{val} must be > 0")
        if self.data["min_customer_age"] > self.data["max_customer_age"]:
            raise ValidationError("min_customer_age > max_customer_age")
        if self.data["claim_reporting_window_days"] < 0:
            raise ValidationError("claim_reporting_window_days must be >= 0")
