# insurance-demo

Insurance policy and claims management system — an in-memory, dependency-free Python 3 (≥3.8) demo.
**All data is mock. Nothing connects to a real insurer, bank or payment provider**
(`MockPaymentGateway` is a local stub).

```bash
python3 -m unittest discover -s . -t . -v   # 80 tests
python3 demo.py                             # seeds mock data, prints all reports as JSON
```

## Modules (`insurance/`)

| Module | Responsibility |
|---|---|
| `customers.py` | Profiles, contact validation/updates, policy ownership |
| `products.py` | Health / vehicle / life products, coverages, premium config, durations |
| `premium.py` | Premium pipeline: risk factors, discounts, surcharges, tax, instalments |
| `policies.py` | Number generation, issuance, dates, limits, status, approval, cancel |
| `claims.py` | Registration, documents, assessment, approval workflow, history |
| `payments.py` | Premium collection, claim settlement (partial/failed), history |
| `reporting.py` | Active policies, premium collection, claims by status, settlements, approval ratios |
| `config.py` | **All configurable rules** (see `DEFAULTS`); `RulesConfig.from_dict/from_json` |
| `audit.py` | Append-only, SHA-256 hash-chained audit trail (`verify()` detects tampering) |
| `security.py` | Roles, permissions, segregation of duties |
| `mock_data.py` | Fictional seed data used by `demo.py` and tests |

Entry point: `InsuranceSystem` (`insurance/system.py`).

## Workflows

* **Policy**: `issue` → `ISSUED`/`ACTIVE` immediately, or `PENDING_APPROVAL` if total coverage limit >
  `policy_approval_limit`; a *different* UNDERWRITER/APPROVER then `approve`s or `reject`s.
  Dates roll via `refresh_statuses()`: `ISSUED → ACTIVE → EXPIRED`; `cancel` is UNDERWRITER-only.
* **Claim**: `REGISTERED` → (documents complete) `assess` → `REJECTED` (nothing payable) |
  `APPROVED` (≤ `claim_auto_approval_limit`) | `PENDING_APPROVAL` → APPROVER `approve`/`reject`
  (must differ from registrar and assessor) → `PARTIALLY_SETTLED` → `SETTLED`.
* Every mutation writes an audit entry (actor, role, action, entity, details).

Calculations and assumptions: [docs/CALCULATIONS.md](docs/CALCULATIONS.md).
