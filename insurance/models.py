"""Domain entities (plain dataclasses, in-memory)."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional


class ProductType(str, Enum):
    HEALTH = "HEALTH"
    VEHICLE = "VEHICLE"
    LIFE = "LIFE"


class PolicyStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"   # awaiting second-person approval
    ISSUED = "ISSUED"                       # approved, effective date in the future
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"                   # issuance rejected by approver


class ClaimStatus(str, Enum):
    REGISTERED = "REGISTERED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    SETTLED = "SETTLED"


class Frequency(str, Enum):
    ANNUAL = "ANNUAL"
    SEMIANNUAL = "SEMIANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"

    @property
    def months(self) -> int:
        return {"ANNUAL": 12, "SEMIANNUAL": 6, "QUARTERLY": 3, "MONTHLY": 1}[self.value]


class InstallmentStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    PAID = "PAID"


class SettlementStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class Customer:
    id: str
    first_name: str
    last_name: str
    date_of_birth: date
    email: str
    phone: str
    address: str
    national_id: str
    active: bool = True


@dataclass
class Coverage:
    """A coverage offered by a product."""
    code: str
    name: str
    limit: Decimal                      # max payable per policy term, per coverage
    deductible: Decimal = Decimal("0")  # fixed amount borne by the insured per claim
    copay_pct: Decimal = Decimal("0")   # insured's share of the amount after deductible
    waiting_period_days: int = 0        # no claims for losses in first N days of cover
    mandatory: bool = True
    additional_premium: Decimal = Decimal("0")  # annual premium if optional and selected


@dataclass
class Exclusion:
    code: str
    description: str
    keywords: List[str]                 # case-insensitive match against claim description


@dataclass
class Product:
    id: str
    code: str
    name: str
    type: ProductType
    base_annual_premium: Decimal
    coverages: List[Coverage]
    exclusions: List[Exclusion]
    allowed_durations_months: List[int]
    active: bool = True


@dataclass
class PolicyCoverage:
    code: str
    name: str
    limit: Decimal
    deductible: Decimal
    copay_pct: Decimal
    waiting_period_days: int


@dataclass
class Installment:
    number: int
    due_date: date
    amount: Decimal
    paid_amount: Decimal = Decimal("0.00")

    @property
    def status(self) -> InstallmentStatus:
        if self.paid_amount >= self.amount:
            return InstallmentStatus.PAID
        return InstallmentStatus.PARTIAL if self.paid_amount > 0 else InstallmentStatus.PENDING


@dataclass
class PremiumPayment:
    id: str
    policy_id: str
    amount: Decimal
    paid_on: date
    allocations: List[Dict[str, Any]]


@dataclass
class Policy:
    id: str
    number: str
    customer_id: str
    product_id: str
    product_type: ProductType
    coverages: List[PolicyCoverage]
    effective_date: date
    expiry_date: date
    duration_months: int
    risk_profile: Dict[str, str]
    premium: Any                        # PremiumBreakdown
    frequency: Frequency
    installments: List[Installment]
    status: PolicyStatus
    created_by: str
    approved_by: Optional[str] = None
    cancelled_on: Optional[date] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def coverage(self, code: str) -> Optional[PolicyCoverage]:
        return next((c for c in self.coverages if c.code == code), None)

    @property
    def total_coverage_limit(self) -> Decimal:
        return sum((c.limit for c in self.coverages), Decimal("0"))


@dataclass
class Document:
    id: str
    doc_type: str
    filename: str
    sha256: str                         # mock: content hash, no real storage
    uploaded_by: str
    uploaded_at: datetime


@dataclass
class Assessment:
    claimed_amount: Decimal
    coverage_valid: bool
    exclusion_codes: List[str]
    reasons: List[str]
    deductible: Decimal
    copay: Decimal
    limit_remaining: Decimal
    approved_amount: Decimal
    rejected_amount: Decimal
    assessed_by: str
    steps: List[str]


@dataclass
class Claim:
    id: str
    number: str
    policy_id: str
    customer_id: str
    coverage_code: str
    loss_date: date
    description: str
    claimed_amount: Decimal
    status: ClaimStatus
    registered_by: str
    documents: List[Document] = field(default_factory=list)
    assessment: Optional[Assessment] = None
    approved_amount: Decimal = Decimal("0.00")
    rejected_amount: Decimal = Decimal("0.00")
    paid_amount: Decimal = Decimal("0.00")
    decision_by: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def outstanding_amount(self) -> Decimal:
        return self.approved_amount - self.paid_amount


@dataclass
class Settlement:
    id: str
    claim_id: str
    amount: Decimal
    method: str
    reference: str
    status: SettlementStatus
    processed_by: str
    processed_at: datetime
    failure_reason: Optional[str] = None
