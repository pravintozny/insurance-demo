# Calculations and business assumptions

All amounts use `Decimal`; every money line is rounded **half-up to 2 dp** at the step shown.
Percentages are in percent. All rates/thresholds below are defaults in `insurance/config.py`
and can be overridden (`RulesConfig.from_dict`).

## 1. Premium (`premium.py`)

| Step | Formula |
|---|---|
| annual_base | `product.base_annual_premium + Σ additional_premium of selected optional coverages` |
| term_base | `annual_base × duration_months / 12` |
| risk_multiplier | product of every risk-factor multiplier for the product type (unrounded) |
| risk_adjusted | `term_base × risk_multiplier` |
| discount | `risk_adjusted × min(Σ discount %, max_total_discount_pct) / 100` |
| surcharge | `risk_adjusted × min(Σ surcharge %, max_total_surcharge_pct) / 100` |
| subtotal | `risk_adjusted − discount + surcharge` |
| loading | `subtotal × installment_loading_pct[frequency] / 100` |
| net_premium | `subtotal + loading` |
| tax | `net_premium × tax_rate[product type] / 100` |
| **total_premium** | `net_premium + tax` |

Worked example (Health, 41-year-old non-smoker, 12 months, quarterly, optional dental):
`1200 + 150 = 1350`; risk 1.00 × 1.00 → 1350; LOYALTY 5% → −67.50 = 1282.50; loading 3% = 38.48
(38.475 rounds up) → net 1320.98; tax 5% = 66.05 → **1387.03**.

* **Risk factors** (defaults): `age_band` (derived from date of birth on the effective date),
  `smoker` (health/life), `vehicle_age_band` (derived from `vehicle_year`) and `usage` (vehicle).
  Missing or unknown values are rejected rather than defaulted to 1.0.
* **Discounts/surcharges** are applied by code, validated against the product type, additive
  (not compounded), and capped in total. Per-line amounts shown in the breakdown are uncapped;
  the totals use the capped percentage. `MULTI_POLICY` (7.5%) is applied automatically when the
  customer already holds an `ISSUED`/`ACTIVE` policy.
* **Instalments**: `n = duration_months / frequency_months` (duration must divide evenly). Each
  instalment is `total / n` rounded **down** to the cent; the last instalment absorbs the remainder
  so the schedule sums exactly to the total. Due dates are `effective_date + k × frequency_months`
  (day clamped to month end). Instalment 1 is due on the effective date.
* Changing a product's base premium affects only policies priced afterwards.

## 2. Policies (`policies.py`)

* **Number**: `{HLT|VEH|LIF}-{issue year}-{6-digit sequence}`, sequence per prefix per year.
* **Period**: `expiry = effective + duration_months − 1 day` (cover is inclusive of both days).
* **Coverage limit** per coverage = product limit unless a lower override is given (must exceed the
  deductible). `total_coverage_limit = Σ coverage limits` and drives the approval threshold.
* Back-dating is not allowed (`max_backdate_days = 0`).
* Assumption: a policy becomes active on issue/approval without requiring the first payment;
  unpaid instalments show up as overdue in reporting.
* Assumption: cancellation does not compute a refund; instalments of cancelled policies are
  excluded from "billed" but cash already received stays in "collected".

## 3. Claim assessment (`claims.py`)

Prerequisites: claim REGISTERED; all `required_claim_documents` for the product type attached
(otherwise assessment is refused and the claim stays REGISTERED).

1. **Coverage validation** – loss date within policy period and not after the policy's cancellation
   date; loss date not within the coverage's waiting period
   (`loss_date < effective_date + waiting_period_days` ⇒ invalid).
2. **Exclusions** – case-insensitive keyword match of the loss description against each product
   exclusion. Any hit ⇒ fully rejected. (Keyword matching is a deliberate mock simplification.)
3. **Remaining limit** – `coverage limit − Σ approved amounts of the other PENDING_APPROVAL/APPROVED/
   PARTIALLY_SETTLED/SETTLED claims` on the same policy and coverage (rejected claims consume nothing).
4. **Amounts** (if steps 1–2 pass):
   ```
   deductible = min(coverage.deductible, claimed)
   after_ded  = claimed − deductible
   copay      = round(after_ded × copay_pct / 100)
   payable    = after_ded − copay
   approved   = min(payable, remaining limit)
   rejected   = claimed − approved
   ```
   Example: claimed 4500, deductible 500, co-pay 10% → 4000 − 400 = **3600 approved, 900 rejected**.
5. **Routing**: `approved = 0` ⇒ REJECTED; `≤ claim_auto_approval_limit` ⇒ APPROVED;
   otherwise PENDING_APPROVAL for a second person with the APPROVER role. A reviewer rejection
   sets approved = 0 and rejected = claimed.

Deductible is per claim (not annual aggregate). Claim must be reported within
`claim_reporting_window_days` (90) of the loss date.

## 4. Settlement (`payments.py`)

* Only APPROVED / PARTIALLY_SETTLED claims can be paid; `amount ≤ approved − paid`.
* A completed payment adds to `paid_amount`; status becomes PARTIALLY_SETTLED, or SETTLED when the
  outstanding amount reaches zero. A failed mock-gateway attempt is kept in history but changes nothing.
* Premium payments are allocated to the oldest unpaid instalment first and cannot exceed the
  outstanding premium.

## 5. Reports (`reporting.py`)

| Report | Definition |
|---|---|
| Active policies | Count, Σ coverage limit, Σ total premium of `ACTIVE` policies, by product type |
| Premium collection | billed = Σ instalments of non-cancelled, non-pending policies; due = instalments with `due_date ≤ as_of`; overdue = unpaid part of due; collected = cash received; outstanding = unpaid billed; rate = (due − overdue)/due |
| Claims by status | Count per `ClaimStatus` |
| Settlement amounts | approved total, completed payments total, outstanding, failed attempts |
| Approval ratio | Over *decided* claims: `approved / (approved + rejected)` by count and by amount (approved amount ÷ claimed amount of decided claims). Pending/registered claims are excluded. `None` when no decisions exist |

## 6. Governance

* Roles: ADMIN (all), AGENT, UNDERWRITER, ADJUSTER, APPROVER, FINANCE (`security.py`).
* Segregation of duties: policy approvers differ from the issuer; claim reviewers differ from the
  registrar and assessor.
* Audit log entries are hash-chained; `AuditLog.verify()` returns False if any entry is altered.
* Not modelled (demo scope): persistence, concurrency, currencies other than one configured label,
  real identity/authentication, real document storage (only a SHA-256 of mock content is kept).
