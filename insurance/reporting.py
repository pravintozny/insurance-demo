"""Management reports. All figures derived from current state; money as Decimal."""
from collections import Counter, defaultdict
from decimal import Decimal

from .models import ClaimStatus, PolicyStatus, SettlementStatus

ZERO = Decimal("0.00")


def _ratio(num, den):
    return (Decimal(num) / Decimal(den)).quantize(Decimal("0.0001")) if den else None


class ReportService:
    def __init__(self, ctx):
        self.ctx = ctx

    def active_policies(self):
        pols = [p for p in self.ctx.policies.values() if p.status == PolicyStatus.ACTIVE]
        by_type = defaultdict(lambda: {"count": 0, "total_coverage_limit": ZERO, "total_premium": ZERO})
        for p in pols:
            row = by_type[p.product_type.value]
            row["count"] += 1
            row["total_coverage_limit"] += p.total_coverage_limit
            row["total_premium"] += p.premium.total_premium
        return {"count": len(pols), "by_product_type": dict(by_type),
                "total_coverage_limit": sum((p.total_coverage_limit for p in pols), ZERO),
                "total_premium": sum((p.premium.total_premium for p in pols), ZERO)}

    def premium_collection(self, as_of=None):
        """Billed = all instalments of non-cancelled/rejected policies;
        due = billed instalments with due_date <= as_of; collected = cash received."""
        as_of = as_of or self.ctx.today()
        billed = due = collected = overdue = outstanding = ZERO
        for p in self.ctx.policies.values():
            if p.status in (PolicyStatus.PENDING_APPROVAL, PolicyStatus.REJECTED):
                continue
            for i in p.installments:
                collected += i.paid_amount
                if p.status == PolicyStatus.CANCELLED:
                    continue
                billed += i.amount
                outstanding += i.amount - i.paid_amount
                if i.due_date <= as_of:
                    due += i.amount
                    overdue += i.amount - i.paid_amount
        return {"billed": billed, "due_to_date": due, "collected": collected,
                "overdue": overdue, "outstanding": outstanding,
                "collection_rate_on_due": _ratio(due - overdue, due)}

    def claims_by_status(self):
        c = Counter(cl.status.value for cl in self.ctx.claims.values())
        return {s.value: c.get(s.value, 0) for s in ClaimStatus}

    def settlement_amounts(self):
        done = [s for s in self.ctx.settlements.values() if s.status == SettlementStatus.COMPLETED]
        failed = [s for s in self.ctx.settlements.values() if s.status == SettlementStatus.FAILED]
        approved = sum((c.approved_amount for c in self.ctx.claims.values()
                        if c.status in (ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_SETTLED,
                                        ClaimStatus.SETTLED)), ZERO)
        paid = sum((s.amount for s in done), ZERO)
        return {"approved_total": approved, "paid_total": paid, "outstanding": approved - paid,
                "completed_payments": len(done), "failed_payments": len(failed),
                "failed_amount": sum((s.amount for s in failed), ZERO)}

    def claim_approval_ratio(self):
        """Over decided claims (approved-family vs REJECTED). Pending/registered excluded."""
        approved_states = (ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_SETTLED, ClaimStatus.SETTLED)
        claims = list(self.ctx.claims.values())
        ok = [c for c in claims if c.status in approved_states]
        no = [c for c in claims if c.status == ClaimStatus.REJECTED]
        claimed = sum((c.claimed_amount for c in ok + no), ZERO)
        approved_amt = sum((c.approved_amount for c in ok), ZERO)
        return {"approved_count": len(ok), "rejected_count": len(no),
                "count_ratio": _ratio(len(ok), len(ok) + len(no)),
                "claimed_amount_decided": claimed, "approved_amount": approved_amt,
                "amount_ratio": _ratio(approved_amt, claimed)}

    def summary(self):
        return {"active_policies": self.active_policies(),
                "premium_collection": self.premium_collection(),
                "claims_by_status": self.claims_by_status(),
                "settlement_amounts": self.settlement_amounts(),
                "claim_approval_ratio": self.claim_approval_ratio()}
