# Payroll start and delivery-report follow-up — WIP, not deployed

Task: OPERATIONAL-FINANCIAL-POSITION-20261101 follow-up.
Repository: AMASI-SA/AMASI-SA.
Branch: fix/operational-financial-followup-20261101.
Parent task: Draft PR #1675, source a8ffd5a81637bed8c4462f7bb4c2b059782cd520.
This isolated branch is a child of that exact checkpoint, not a replacement for its production integration.

## Owner requirements

Delivery after operational startup earns the actual driver's/carrier's fee regardless of customer order creation date. Newly approved supplier invoices are likewise independent of customer order age. Those recognition changes are inherited from the parent. Salaries must accrue from the first day of the opening month, monthly salary divided by the actual number of calendar days, with no duplicate daily accrual.

## Implemented here

- `operational_salary_start.py`: owner-only, previewed first-of-opening-month payroll policy. The preview exposes only missing daily obligations by currency. Approval pins source/opening evidence and a session scope, rechecks owner/financial phase, records an idempotent audited policy, and does not change cash, opening balances or existing salary records. The owner must explicitly establish that opening balances exclude wages for the newly included period. No live approval has been performed.
- `operational_balance_engine.py`: uses the approved inclusive payroll cutoff. Tenants without an approved policy retain their old cutoff. Invalid policy evidence produces an issue and preserves recorded salary days/conflicts. Calendar-day proration, employment eligibility, effective salary changes, leave and cumulative rounding remain owned by the existing salary projection.
- `operational_balance_routes.py`: protected POST `/salary-start/preview` and `/salary-start/approve`; strict confirmation boolean; native mobile setup routes remain denied. Staff cannot authorize the policy. An owner UI is still needed.
- Delivery report diagnostics now distinguish support for post-start delivery events from the still-incomplete noncash bank/POS posting path, using the exact saved source marker. Report presentation does not create money.
- Source files are wired normally in the candidate. No runtime patch application, monkey-patching, generated-source substitution or production data rewrite is involved.

## Local verification

The exact upstream source snapshot was obtained through a contents-read-only GitHub Actions archive, and 4,542 unpacked files were checked against `git ls-tree` blob hashes. No production environment or database was loaded. Runtime test dependencies were downloaded by a separate contents-read-only job from the public package index and installed in an isolated local target.

Latest local combined command returned **308 passed**, exit 0, zero skips/failures/errors. This covers payroll/rounding/source/supplier/delivery/report regressions with mock databases where relevant. It does not claim real Mongo acceptance.

```bash
PYTHONPATH=/mnt/data/mezan_work/testdeps:backend:backend/tests python -B -m pytest --noconftest -q \
 backend/tests/test_operational_balance_salary_start.py \
 backend/tests/test_operational_balance_salary_consistency.py \
 backend/tests/test_operational_balance_engine.py \
 backend/tests/test_operational_balance_financial_position.py \
 backend/tests/test_operational_balance_delivery_report_coverage.py \
 backend/tests/test_operational_supplier_invoice_cutover.py \
 backend/tests/test_operational_balance_sources.py \
 backend/tests/test_operational_balance_source_fixes.py \
 backend/tests/test_operational_balance_delivery_evidence.py --tb=short
```

AST parsing and diff whitespace checks passed. Local Python was 3.13.5; isolated CI targets the repository's Python 3.11 test contract.

Four new disposable-Mongo acceptance tests cover concurrent approval/refresh, stale-source refusal, revoked-owner refusal, and owner-only HTTP preview/approval/retry. They have been written but are pending execution at this checkpoint. The new contents-read-only candidate workflow will run the full operational suite and those tests on a localhost Mongo 8.0.12 replica set. Do not claim acceptance until its result is read back.

## Exact candidate application blobs

- backend/operational_balance_engine.py: 91df8bf6af45bb256b23e153af4f68e1913e3b8c
- backend/operational_balance_routes.py: f8cf432ff6ef7ce2dff623c715affbb296e25769
- backend/operational_financial_position.py: 56879afb81952ea1aa8e0362cfba6923926c0b0e
- backend/operational_delivery_report_coverage.py: d7ffcf824775d26a5332eba8edb500c1d68284a9
- backend/operational_salary_start.py: 308be77eda0b28b22cdc01716aef6527a314bae4

## Prerequisite and remaining work

PR #1676 was observed merged at ffa145f22dbf4167649736336083d0d16f9fed27. That is NOT proof of live publication of source 667d94a3b01b7a59f58b753dfbf7d52c80764e02. No matching live v5 verification was established in this session. PR #1675 remains Draft and was observed with merge conflicts against its base. No prerequisite merge/rebase or production integration has been performed here.

Remaining: owner payroll preview/approval UI and actual opening-coverage review; operational driver bank/POS approval writer and verified report linkage; full pre-start COD forecasts; consistent profit-period coverage; integration with the exact published cost prerequisite and updated dependency graph; full combined frontend/security/build/visual/export acceptance. The financial report must keep unsupported totals unavailable.

Production changed: **no**. Production database/ledger/openings changed: **no**. `/app`, release intent, guard lease and deployment: **untouched**. Original task branch and PR #1675: **not rewritten**. Do not merge or deploy this draft as a standalone production release.

Next safe action: read the exact follow-up head and candidate CI output, resolve any failures, then preserve a verified checkpoint. Continue the unfinished integration only on isolated branches. Issue #1006 is full; this record and the task PR are the continuation authority.
