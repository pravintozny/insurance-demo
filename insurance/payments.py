"""Premium collection and claim settlement (mock gateway only)."""
from datetime import date
from decimal import Decimal

from .errors import StateError, ValidationError
from .models import (ClaimStatus, PolicyStatus, PremiumPayment, Settlement, SettlementStatus)
from .security import require
from .util import money


class MockPaymentGateway:
    """Stand-in for a bank/PSP. Never touches a network.

    A payment fails when its reference is in `fail_references`, or when
    `fail_over` is set and the amount exceeds it."""

    def __init__(self, fail_references=(), fail_over=None):
        self.fail_references = set(fail_references)
        self.fail_over = fail_over

    def pay(self, amount, method, reference):
        if reference in self.fail_references:
            return False, "declined by mock gateway"
        if self.fail_over is not None and amount > self.fail_over:
            return False, "amount exceeds mock gateway limit"
        return True, None


class PaymentService:
    def __init__(self, ctx, gateway=None):
        self.ctx = ctx
        self.gateway = gateway or MockPaymentGateway()

    # -- premiums ------------------------------------------------------------
    def collect_premium(self, actor, policy_id, amount, paid_on=None) -> PremiumPayment:
        """Allocate a payment to the oldest unpaid instalments first."""
        require(actor, "premium.collect")
        policy = self.ctx.get(self.ctx.policies, policy_id, "policy")
        if policy.status in (PolicyStatus.PENDING_APPROVAL, PolicyStatus.REJECTED,
                             PolicyStatus.CANCELLED):
            raise StateError(f"cannot collect premium on a {policy.status.value} policy")
        amount = money(amount)
        if amount <= 0:
            raise ValidationError("payment amount must be positive")
        due = sum((i.amount - i.paid_amount for i in policy.installments), Decimal("0"))
        if amount > due:
            raise ValidationError(f"payment {amount} exceeds outstanding premium {due}")
        left, allocations = amount, []
        for inst in policy.installments:
            room = inst.amount - inst.paid_amount
            if room <= 0 or left <= 0:
                continue
            take = min(room, left)
            inst.paid_amount += take
            left -= take
            allocations.append({"installment": inst.number, "amount": take})
        pay = PremiumPayment(self.ctx.ids.next("PRM"), policy.id, amount,
                             paid_on or self.ctx.today(), allocations)
        self.ctx.premium_payments[pay.id] = pay
        self.ctx.audit.record(actor, "premium.collected", "policy", policy.id,
                              {"amount": str(amount), "allocations": [
                                  {"installment": a["installment"], "amount": str(a["amount"])}
                                  for a in allocations]})
        return pay

    def outstanding_premium(self, policy_id) -> Decimal:
        p = self.ctx.get(self.ctx.policies, policy_id, "policy")
        return sum((i.amount - i.paid_amount for i in p.installments), Decimal("0"))

    # -- claim settlement ------------------------------------------------------
    def settle_claim(self, actor, claim_id, amount, method="BANK_TRANSFER", reference=None) -> Settlement:
        """Pay all or part of the approved amount. A failed attempt is recorded
        but does not reduce the outstanding balance."""
        require(actor, "claim.settle")
        claim = self.ctx.get(self.ctx.claims, claim_id, "claim")
        if claim.status not in (ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_SETTLED):
            raise StateError(f"claim is {claim.status.value}; only approved claims can be settled")
        amount = money(amount)
        if amount <= 0:
            raise ValidationError("settlement amount must be positive")
        if amount > claim.outstanding_amount:
            raise ValidationError(
                f"settlement {amount} exceeds outstanding approved amount {claim.outstanding_amount}")
        sid = self.ctx.ids.next("STL")
        reference = reference or sid
        ok, reason = self.gateway.pay(amount, method, reference)
        st = Settlement(sid, claim.id, amount, method, reference,
                        SettlementStatus.COMPLETED if ok else SettlementStatus.FAILED,
                        actor.name, self.ctx.clock(), reason)
        self.ctx.settlements[sid] = st
        if ok:
            claim.paid_amount += amount
            new = (ClaimStatus.SETTLED if claim.outstanding_amount == 0
                   else ClaimStatus.PARTIALLY_SETTLED)
            claim.history.append({"at": self.ctx.clock().isoformat(), "from": claim.status.value,
                                  "to": new.value, "by": actor.name,
                                  "note": f"settlement {sid} of {amount}"})
            claim.status = new
        self.ctx.audit.record(actor, "claim.settlement_" + st.status.value.lower(), "claim",
                              claim.id, {"settlement": sid, "amount": str(amount), "reason": reason})
        return st

    def settlement_history(self, claim_id):
        self.ctx.get(self.ctx.claims, claim_id, "claim")
        return [s for s in self.ctx.settlements.values() if s.claim_id == claim_id]
