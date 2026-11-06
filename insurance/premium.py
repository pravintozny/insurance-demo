"""Premium calculation. See docs/CALCULATIONS.md for the full specification.

Pipeline (each money line rounded half-up to 2 dp):
  annual_base   = product.base_annual_premium + sum(optional coverage additional premiums)
  term_base     = annual_base * months / 12
  risk_adjusted = term_base * product(risk multipliers)
  discount      = risk_adjusted * min(sum(discount %), cap) / 100
  surcharge     = risk_adjusted * min(sum(surcharge %), cap) / 100
  subtotal      = risk_adjusted - discount + surcharge
  loading       = subtotal * instalment loading % / 100
  net           = subtotal + loading
  tax           = net * tax rate / 100
  total         = net + tax        -> split into instalments
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_DOWN
from typing import List

from .errors import ValidationError
from .models import Frequency, Installment, ProductType
from .util import D, add_months, age_on, money


@dataclass
class PremiumBreakdown:
    annual_base: Decimal
    term_base: Decimal
    risk_factors: List[dict]
    risk_multiplier: Decimal
    risk_adjusted: Decimal
    discounts: List[dict]
    discount_pct_applied: Decimal
    discount_amount: Decimal
    surcharges: List[dict]
    surcharge_pct_applied: Decimal
    surcharge_amount: Decimal
    subtotal: Decimal
    installment_loading_pct: Decimal
    loading_amount: Decimal
    net_premium: Decimal
    tax_rate_pct: Decimal
    tax_amount: Decimal
    total_premium: Decimal
    frequency: Frequency
    installments: List[Installment] = field(default_factory=list)


def _band(bands, value, label):
    for upper, name in bands:
        if upper is None or value <= upper:
            return name
    raise ValidationError(f"no {label} band for {value}")  # pragma: no cover


class PremiumCalculator:
    def __init__(self, config):
        self.cfg = config

    # -- risk profile ------------------------------------------------------
    def derive_profile(self, ptype: ProductType, customer, profile: dict, on: date) -> dict:
        """Add derived attributes (age_band, vehicle_age_band) to supplied attributes."""
        out = {k: str(v).lower() if k in ("smoker", "usage") else str(v)
               for k, v in (profile or {}).items()}
        out["age_band"] = _band(self.cfg["age_bands"][ptype.value],
                                age_on(customer.date_of_birth, on), "age")
        if ptype == ProductType.VEHICLE:
            if "vehicle_year" not in out:
                raise ValidationError("vehicle_year is required for vehicle products")
            try:
                vage = on.year - int(out["vehicle_year"])
            except ValueError:
                raise ValidationError("vehicle_year must be a year") from None
            if vage < 0:
                raise ValidationError("vehicle_year is in the future")
            out["vehicle_age_band"] = _band(self.cfg["vehicle_age_bands"], vage, "vehicle age")
        return out

    def _risk_lines(self, ptype, profile):
        lines = []
        for factor, table in self.cfg["risk_factors"][ptype.value].items():
            if factor not in profile:
                raise ValidationError(f"risk attribute {factor!r} is required for {ptype.value}")
            value = profile[factor]
            if value not in table:
                raise ValidationError(
                    f"invalid value {value!r} for {factor}; allowed: {sorted(table)}")
            lines.append({"factor": factor, "value": value, "multiplier": D(table[value])})
        return lines

    def _adjustments(self, kind, ptype, codes, auto_codes=()):
        table = self.cfg[kind]
        lines, seen = [], set()
        for code in list(codes) + list(auto_codes):
            if code in seen:
                continue
            seen.add(code)
            if code not in table:
                raise ValidationError(f"unknown {kind[:-1]} code: {code}")
            if ptype.value not in table[code]["applies_to"]:
                raise ValidationError(f"{code} does not apply to {ptype.value} products")
            lines.append({"code": code, "pct": D(table[code]["pct"])})
        return lines

    # -- main --------------------------------------------------------------
    def calculate(self, product, customer, duration_months, effective_date, risk_profile,
                  selected_optional=(), discount_codes=(), surcharge_codes=(),
                  frequency=Frequency.ANNUAL, auto_discount_codes=()) -> PremiumBreakdown:
        frequency = Frequency(frequency)
        if duration_months not in product.allowed_durations_months:
            raise ValidationError(
                f"duration {duration_months} not allowed; use {product.allowed_durations_months}")
        if duration_months % frequency.months:
            raise ValidationError(
                f"{duration_months}-month term cannot be split into {frequency.value} instalments")
        optional = {c.code: c for c in product.coverages if not c.mandatory}
        for code in selected_optional:
            if code not in optional:
                raise ValidationError(f"{code} is not an optional coverage of {product.code}")

        annual_base = money(product.base_annual_premium +
                            sum((optional[c].additional_premium for c in set(selected_optional)),
                                Decimal("0")))
        term_base = money(annual_base * duration_months / 12)

        risk = self._risk_lines(product.type, risk_profile)
        multiplier = Decimal("1")
        for line in risk:
            multiplier *= line["multiplier"]
        risk_adjusted = money(term_base * multiplier)

        discounts = self._adjustments("discounts", product.type, discount_codes, auto_discount_codes)
        d_pct = min(sum((x["pct"] for x in discounts), Decimal("0")),
                    self.cfg.pct("max_total_discount_pct"))
        for x in discounts:
            x["amount"] = money(risk_adjusted * x["pct"] / 100)
        discount_amount = money(risk_adjusted * d_pct / 100)

        surcharges = self._adjustments("surcharges", product.type, surcharge_codes)
        s_pct = min(sum((x["pct"] for x in surcharges), Decimal("0")),
                    self.cfg.pct("max_total_surcharge_pct"))
        for x in surcharges:
            x["amount"] = money(risk_adjusted * x["pct"] / 100)
        surcharge_amount = money(risk_adjusted * s_pct / 100)

        subtotal = risk_adjusted - discount_amount + surcharge_amount
        load_pct = self.cfg.pct("installment_loading_pct", frequency.value)
        loading = money(subtotal * load_pct / 100)
        net = subtotal + loading
        tax_pct = self.cfg.pct("tax_rates", product.type.value)
        tax = money(net * tax_pct / 100)
        total = net + tax

        bd = PremiumBreakdown(annual_base, term_base, risk, multiplier, risk_adjusted,
                              discounts, d_pct, discount_amount, surcharges, s_pct,
                              surcharge_amount, subtotal, load_pct, loading, net, tax_pct,
                              tax, total, frequency)
        bd.installments = self.schedule(total, effective_date, duration_months, frequency)
        return bd

    @staticmethod
    def schedule(total: Decimal, start: date, duration_months: int, frequency: Frequency):
        """Equal instalments (rounded down to the cent); the last absorbs the remainder.
        Instalment 1 is due on the effective date."""
        n = duration_months // frequency.months
        base = (total / n).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        out = [Installment(i + 1, add_months(start, i * frequency.months), base)
               for i in range(n)]
        out[-1].amount = total - base * (n - 1)
        return out
