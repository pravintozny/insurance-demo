"""Claims registration, documents, assessment and approval workflow."""
import hashlib
from datetime import timedelta
from decimal import Decimal

from .errors import PermissionDenied, StateError, ValidationError
from .models import Assessment, Claim, ClaimStatus, Document, PolicyStatus
from .security import require
from .util import D, money

COMMITTED = (ClaimStatus.PENDING_APPROVAL, ClaimStatus.APPROVED,
             ClaimStatus.PARTIALLY_SETTLED, ClaimStatus.SETTLED)
FINAL = (ClaimStatus.REJECTED, ClaimStatus.SETTLED)
DOC_TYPES = {"CLAIM_FORM", "MEDICAL_REPORT", "POLICE_REPORT", "DEATH_CERTIFICATE",
             "INVOICE", "PHOTO", "OTHER"}


class ClaimService:
    def __init__(self, ctx):
        self.ctx = ctx

    def get(self, claim_id) -> Claim:
        return self.ctx.get(self.ctx.claims, claim_id, "claim")

    def _transition(self, claim, new, actor, note=""):
        claim.history.append({"at": self.ctx.clock().isoformat(), "from": claim.status.value if claim.history else None,
                              "to": new.value, "by": actor.name, "note": note})
        claim.status = new

    # -- registration ------------------------------------------------------
    def register(self, actor, policy_id, coverage_code, loss_date, description,
                 claimed_amount, documents=()) -> Claim:
        require(actor, "claim.register")
        policy = self.ctx.get(self.ctx.policies, policy_id, "policy")
        if policy.status not in (PolicyStatus.ACTIVE, PolicyStatus.EXPIRED):
            raise StateError(f"claims cannot be registered on a {policy.status.value} policy")
        if policy.coverage(coverage_code) is None:
            raise ValidationError(f"coverage {coverage_code} is not part of policy {policy.number}")
        today = self.ctx.today()
        if loss_date > today:
            raise ValidationError("loss date cannot be in the future")
        if not (policy.effective_date <= loss_date <= policy.expiry_date):
            raise ValidationError("loss date is outside the policy period")
        if (today - loss_date).days > self.ctx.config["claim_reporting_window_days"]:
            raise ValidationError(
                f"claim reported more than {self.ctx.config['claim_reporting_window_days']} days after loss")
        if not description or not description.strip():
            raise ValidationError("a loss description is required")
        amount = money(claimed_amount)
        if amount <= 0:
            raise ValidationError("claimed amount must be positive")
        claim = Claim(self.ctx.ids.next("CLM"), "", policy.id, policy.customer_id,
                      coverage_code, loss_date, description.strip(), amount,
                      ClaimStatus.REGISTERED, actor.name)
        claim.number = "CLM-" + claim.id.split("-")[1]
        self._transition(claim, ClaimStatus.REGISTERED, actor, "registered")
        self.ctx.claims[claim.id] = claim
        self.ctx.audit.record(actor, "claim.registered", "claim", claim.id, {
            "policy": policy.number, "coverage": coverage_code, "claimed": str(amount)})
        for doc_type, filename, content in documents:
            self.add_document(actor, claim.id, doc_type, filename, content)
        return claim

    def add_document(self, actor, claim_id, doc_type, filename, content: bytes = b"") -> Document:
        require(actor, "claim.register")
        claim = self.get(claim_id)
        if claim.status in FINAL or claim.status != ClaimStatus.REGISTERED:
            raise StateError(f"documents cannot be added to a {claim.status.value} claim")
        if doc_type not in DOC_TYPES:
            raise ValidationError(f"unknown document type {doc_type!r}; allowed: {sorted(DOC_TYPES)}")
        if not filename:
            raise ValidationError("filename is required")
        if isinstance(content, str):
            content = content.encode()
        doc = Document(self.ctx.ids.next("DOC"), doc_type, filename,
                       hashlib.sha256(content).hexdigest(), actor.name, self.ctx.clock())
        claim.documents.append(doc)
        self.ctx.audit.record(actor, "claim.document_added", "claim", claim.id,
                              {"type": doc_type, "file": filename, "sha256": doc.sha256})
        return doc

    def missing_documents(self, claim):
        policy = self.ctx.policies[claim.policy_id]
        have = {d.doc_type for d in claim.documents}
        return [d for d in self.ctx.config["required_claim_documents"][policy.product_type.value]
                if d not in have]

    # -- assessment --------------------------------------------------------
    def assess(self, actor, claim_id) -> Claim:
        """Validate coverage, exclusions, apply deductible/co-pay/limit.

        Steps are recorded in Assessment.steps for explainability."""
        require(actor, "claim.assess")
        claim = self.get(claim_id)
        if claim.status != ClaimStatus.REGISTERED:
            raise StateError(f"claim is {claim.status.value}; only REGISTERED claims can be assessed")
        missing = self.missing_documents(claim)
        if missing:
            raise StateError(f"missing required documents: {missing}")
        policy = self.ctx.policies[claim.policy_id]
        product = self.ctx.products[policy.product_id]
        cov = policy.coverage(claim.coverage_code)
        claimed = claim.claimed_amount
        steps, reasons, hits = [], [], []

        # 1. Coverage validation
        if policy.cancelled_on and claim.loss_date > policy.cancelled_on:
            reasons.append("loss occurred after policy cancellation")
        if not (policy.effective_date <= claim.loss_date <= policy.expiry_date):
            reasons.append("loss outside policy period")  # defensive; re-checked at assessment
        waiting_end = policy.effective_date + timedelta(days=cov.waiting_period_days)
        if claim.loss_date < waiting_end:
            reasons.append(f"loss within {cov.waiting_period_days}-day waiting period")
        coverage_ok = not reasons
        steps.append(f"coverage {cov.code}: {'valid' if coverage_ok else 'INVALID'}")

        # 2. Exclusion validation (keyword match on loss description)
        text = claim.description.lower()
        for ex in product.exclusions:
            if any(k.lower() in text for k in ex.keywords):
                hits.append(ex.code)
                reasons.append(f"excluded: {ex.description} ({ex.code})")
        steps.append(f"exclusions hit: {hits or 'none'}")

        # 3. Limit already consumed by other committed claims on this policy+coverage
        used = sum((c.approved_amount for c in self.ctx.claims.values()
                    if c.id != claim.id and c.policy_id == policy.id
                    and c.coverage_code == cov.code and c.status in COMMITTED), Decimal("0"))
        remaining = max(cov.limit - used, Decimal("0"))
        steps.append(f"limit {cov.limit} - used {used} = remaining {remaining}")

        deductible = copay = Decimal("0.00")
        approved = Decimal("0.00")
        if not reasons:
            # 4. Deductible, 5. co-pay, 6. cap by remaining limit
            deductible = min(cov.deductible, claimed)
            after_ded = claimed - deductible
            copay = money(after_ded * cov.copay_pct / 100)
            payable = after_ded - copay
            approved = money(min(payable, remaining))
            steps.append(f"claimed {claimed} - deductible {deductible} = {after_ded}; "
                         f"co-pay {cov.copay_pct}% = {copay}; payable {payable}")
            if payable > remaining:
                reasons.append(f"capped by remaining coverage limit {remaining}")
                steps.append(f"capped to {approved}")
            if approved == 0:
                reasons.append("nothing payable after deductible/limit")
        rejected = claimed - approved
        claim.assessment = Assessment(claimed, coverage_ok,
                                      hits, reasons, deductible, copay, remaining, approved,
                                      rejected, actor.name, steps)
        claim.approved_amount, claim.rejected_amount = approved, rejected
        claim.decision_by = actor.name
        self.ctx.audit.record(actor, "claim.assessed", "claim", claim.id, {
            "approved": str(approved), "rejected": str(rejected), "reasons": reasons})

        # Outcome / workflow routing
        if approved == 0:
            self._transition(claim, ClaimStatus.REJECTED, actor, "; ".join(reasons))
            self.ctx.audit.record(actor, "claim.rejected", "claim", claim.id, {"by": "assessment"})
        elif approved <= D(self.ctx.config["claim_auto_approval_limit"]):
            self._transition(claim, ClaimStatus.APPROVED, actor, "auto-approved within limit")
            self.ctx.audit.record(actor, "claim.approved", "claim", claim.id, {"mode": "auto"})
        else:
            self._transition(claim, ClaimStatus.PENDING_APPROVAL, actor,
                             "approved amount above auto-approval limit")
        return claim

    # -- approval workflow ---------------------------------------------------
    def _check_reviewer(self, actor, claim):
        require(actor, "claim.approve")
        if claim.status != ClaimStatus.PENDING_APPROVAL:
            raise StateError(f"claim is {claim.status.value}, not pending approval")
        if actor.name == claim.decision_by or actor.name == claim.registered_by:
            raise PermissionDenied("segregation of duties: reviewer must differ from registrar/assessor")

    def approve(self, actor, claim_id, note="") -> Claim:
        claim = self.get(claim_id)
        self._check_reviewer(actor, claim)
        claim.decision_by = actor.name
        self._transition(claim, ClaimStatus.APPROVED, actor, note or "approved")
        self.ctx.audit.record(actor, "claim.approved", "claim", claim.id,
                              {"mode": "manual", "amount": str(claim.approved_amount)})
        return claim

    def reject(self, actor, claim_id, reason) -> Claim:
        claim = self.get(claim_id)
        self._check_reviewer(actor, claim)
        if not reason:
            raise ValidationError("a rejection reason is required")
        claim.decision_by = actor.name
        claim.rejected_amount, claim.approved_amount = claim.claimed_amount, Decimal("0.00")
        self._transition(claim, ClaimStatus.REJECTED, actor, reason)
        self.ctx.audit.record(actor, "claim.rejected", "claim", claim.id,
                              {"by": "reviewer", "reason": reason})
        return claim
