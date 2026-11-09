# Historical prepaid capture performance study — 2026-11-09

NONDEPLOYABLE HISTORICAL PATCH. Base: `de76aed339be775e2878071c92063d0ab9df06df`.
This is not a restoration or production candidate. Preserve current production,
courier changes, rollback, original A/B commits and release guard. Extract only the
reviewed minimal patch if the audit feature is separately restored and verified.
No /app operations, deployment, live data access, financial writes or backfill.

## Change

`prepaid_shipping_paid` previously fell through to a full financial report for
ordinary courier balances on every charge. Reuse the existing exact ordinary debt
projection for courier bank/cash rows. Driver eligibility and arithmetic are unchanged.
Supplier/provider kinds, funding and derived-credit models retain full-report fallback.
No cache across events, zero substitution, completeness override or balance mutation.

## Verification

Baseline regression test failed because the full report was called. After the patch:
134 tests passed in 11.34s across the seven suites below. An initial collection attempt
failed for missing test-only `mongomock_motor`; installed `mongomock-motor==0.0.36`
in the isolated test venv, then reran successfully. No tests were disabled.

200 seeded mixed states compare complete snapshots (including seals) with forced
full-report capture: SAR/USD, bank/cash, FX, unknown opening, opening debt,
receivables/payables, superseded/incomplete obligations, corrections, advances,
overallocations, refunds and credit identities. Four special-model cases assert the
full-report fallback. A 100-event prefix test with 5,000 prior movements forbids full
reports and verifies the exact cash balance after each charge, courier net and no
capture mutation to input state.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest \
 backend/tests/test_operational_balance_activity_prepaid_projection.py \
 backend/tests/test_operational_balance_activity_driver_projection.py \
 backend/tests/test_operational_balance_activity_cash_projection.py \
 backend/tests/test_operational_balance_prepaid_position.py \
 backend/tests/test_operational_balance_prepaid_shipping.py \
 backend/tests/test_operational_balance_prepaid_shipment_chronology.py \
 backend/tests/test_operational_balance_prepaid_tariffs.py -q --tb=short
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 python scripts/benchmark_prepaid_capture_projection.py
```

## Synthetic benchmark

100 charges over 5,000 prior movements; three runs; historical implementation loaded
from the pinned base above; network blocked. Identical entire state and all 100 prefix
snapshots, including snapshot seals, before/after in every run.

| Measurement | Historical | Candidate |
| --- | ---: | ---: |
| Run 1 seconds | 7.567460 | 0.204883 |
| Run 2 seconds | 7.750663 | 0.192317 |
| Run 3 seconds | 8.085980 | 0.205564 |
| Median seconds | 7.750663 | 0.204883 |
| Full-report calls per run | 100 | 0 |

Median reduction about 97.4%. No runtime threshold replaces correctness assertions.
This establishes a synthetic hotspot and its isolated fix, not proof that this code
caused the historical slowdown, and not a production load result. The scheduled
reconciliation runs in a separate background process from web requests but shares
its async loop with preparation jobs; production causal attribution remains unverified.

## Remaining before any restoration

Independent review, transplant onto latest approved production with compatible audit
requirements, integrated correctness and performance tests, release guard/V5, and
live verification belong to the parent integration task. Do not merge this historical
branch into production. No completeness or deployment claim is made here.
