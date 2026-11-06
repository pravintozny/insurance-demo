"""Policy issuance and lifecycle."""
from datetime import timedelta

from .errors import PermissionDenied, StateError, ValidationError
from .models import Frequency, Policy, PolicyCoverage, PolicyStatus
from .premium import PremiumCalculator
from .security import SYSTEM, require
from .util import D, expiry_for, money

PREFIX = {"HEALTH": "HLT", "VEHICLE": "VEH", "LIFE": "LIF"}
LIVE = (PolicyStatus.ISSUED, PolicyStatus.ACTIVE)
SOD_MSG = "segregation of duties: approver must differ from the requester"


class PolicyService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calc = PremiumCalculator(ctx.config)

    # -- helpers -----------------------------------------------------------
    def get(self, policy_id) -> Policy:
        return self.ctx.get(self.ctx.policies, policy_id, "policy")

    def _transition(self, policy, new_status, actor, note=""):
        policy.history.append({"at": self.ctx.clock().isoformat(), "from": policy.status.value,
                               "to": new_status.value, "by": actor.name, "note": note})
        policy.status = new_status

    def _next_number(self, ptype):
        prefix, year = PREFIX[ptype.value], self.ctx.today().year
        n = self.ctx._policy_seq.get((prefix, year), 0) + 1
        self.ctx._policy_seq[(prefix, year)] = n
        return f"{prefix}-{year}-{n:06d}"

    def _status_for_dates(self, policy, today):
        if today > policy.expiry_date:
            return PolicyStatus.EXPIRED
        return PolicyStatus.ACTIVE if today >= policy.effective_date else PolicyStatus.ISSUED

    # -- pricing -----------------------------------------------------------
    def quote(self, customer_id, product_id, duration_months, effective_date, risk_profile=None,
              selected_optional=(), discount_codes=(), surcharge_codes=(),
              frequency=Frequency.ANNUAL):
        cust = self.ctx.get(self.ctx.customers, customer_id, "customer")
        prod = self.ctx.get(self.ctx.products, product_id, "product")
        profile = self.calc.derive_profile(prod.type, cust, risk_profile, effective_date)
        auto = []
        if any(p.customer_id == customer_id and p.status in LIVE
               for p in self.ctx.policies.values()):
            auto.append(self.ctx.config["auto_multi_policy_discount"])
        bd = self.calc.calculate(prod, cust, duration_months, effective_date, profile,
                                 selected_optional, discount_codes, surcharge_codes,
                                 frequency, auto)
        return bd, profile

    # -- issuance ----------------------------------------------------------
    def issue(self, actor, customer_id, product_id, duration_months, effective_date,
              risk_profile=None, selected_optional=(), discount_codes=(), surcharge_codes=(),
              frequency=Frequency.ANNUAL, coverage_limits=None) -> Policy:
        require(actor, "policy.issue")
        cust = self.ctx.get(self.ctx.customers, customer_id, "customer")
        prod = self.ctx.get(self.ctx.products, product_id, "product")
        if not cust.active:
            raise ValidationError("customer is inactive")
        if not prod.active:
            raise ValidationError("product is retired")
        today = self.ctx.today()
        if effective_date < today - timedelta(days=self.ctx.config["max_backdate_days"]):
            raise ValidationError("effective date is in the past")

        bd, profile = self.quote(customer_id, product_id, duration_months, effective_date,
                                 risk_profile, selected_optional, discount_codes,
                                 surcharge_codes, frequency)
        coverages = []
        chosen = set(selected_optional)
        limits = dict(coverage_limits or {})
        for c in prod.coverages:
            if not c.mandatory and c.code not in chosen:
                continue
            limit = money(limits.pop(c.code, c.limit))
            if limit <= c.deductible or limit > c.limit:
                raise ValidationError(
                    f"limit for {c.code} must be > deductible and <= product limit {c.limit}")
            coverages.append(PolicyCoverage(c.code, c.name, limit, c.deductible, c.copay_pct,
                                            c.waiting_period_days))
        if limits:
            raise ValidationError(f"coverage_limits for unknown/unselected coverages: {sorted(limits)}")

        policy = Policy(
            id=self.ctx.ids.next("POL"), number=self._next_number(prod.type),
            customer_id=customer_id, product_id=product_id, product_type=prod.type,
            coverages=coverages, effective_date=effective_date,
            expiry_date=expiry_for(effective_date, duration_months),
            duration_months=duration_months, risk_profile=profile, premium=bd,
            frequency=bd.frequency, installments=bd.installments,
            status=PolicyStatus.PENDING_APPROVAL, created_by=actor.name)
        needs_approval = policy.total_coverage_limit > D(self.ctx.config["policy_approval_limit"])
        if needs_approval:
            policy.history.append({"at": self.ctx.clock().isoformat(), "from": None,
                                   "to": "PENDING_APPROVAL", "by": actor.name,
                                   "note": "coverage limit above approval threshold"})
        else:
            policy.status = self._status_for_dates(policy, today)
            policy.approved_by = "auto"
            policy.history.append({"at": self.ctx.clock().isoformat(), "from": None,
                                   "to": policy.status.value, "by": actor.name, "note": "issued"})
        self.ctx.policies[policy.id] = policy
        self.ctx.audit.record(actor, "policy.issued", "policy", policy.id, {
            "number": policy.number, "status": policy.status.value,
            "total_premium": str(bd.total_premium), "coverage_limit": str(policy.total_coverage_limit)})
        return policy

    def approve(self, actor, policy_id) -> Policy:
        require(actor, "policy.approve")
        p = self.get(policy_id)
        if p.status != PolicyStatus.PENDING_APPROVAL:
            raise StateError(f"policy is {p.status.value}, not pending approval")
        if actor.name == p.created_by:
            raise PermissionDenied(SOD_MSG)
        p.approved_by = actor.name
        self._transition(p, self._status_for_dates(p, self.ctx.today()), actor, "approved")
        self.ctx.audit.record(actor, "policy.approved", "policy", p.id, {"status": p.status.value})
        return p

    def reject(self, actor, policy_id, reason) -> Policy:
        require(actor, "policy.approve")
        p = self.get(policy_id)
        if p.status != PolicyStatus.PENDING_APPROVAL:
            raise StateError(f"policy is {p.status.value}, not pending approval")
        if actor.name == p.created_by:
            raise PermissionDenied(SOD_MSG)
        if not reason:
            raise ValidationError("a rejection reason is required")
        self._transition(p, PolicyStatus.REJECTED, actor, reason)
        self.ctx.audit.record(actor, "policy.rejected", "policy", p.id, {"reason": reason})
        return p

    def cancel(self, actor, policy_id, reason, on=None) -> Policy:
        require(actor, "policy.cancel")
        p = self.get(policy_id)
        if p.status not in LIVE:
            raise StateError(f"cannot cancel a {p.status.value} policy")
        if not reason:
            raise ValidationError("a cancellation reason is required")
        p.cancelled_on = on or self.ctx.today()
        self._transition(p, PolicyStatus.CANCELLED, actor, reason)
        self.ctx.audit.record(actor, "policy.cancelled", "policy", p.id, {"reason": reason})
        return p

    def refresh_statuses(self, as_of=None):
        """Roll ISSUED->ACTIVE and ACTIVE->EXPIRED by date. Returns changed policies."""
        today = as_of or self.ctx.today()
        changed = []
        for p in self.ctx.policies.values():
            if p.status in LIVE:
                new = self._status_for_dates(p, today)
                if new != p.status:
                    self._transition(p, new, SYSTEM, "date roll")
                    self.ctx.audit.record(SYSTEM, "policy.status_rolled", "policy", p.id,
                                          {"status": new.value})
                    changed.append(p)
        return changed

