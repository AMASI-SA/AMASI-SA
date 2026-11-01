# Payroll cutover-day and delivery-report follow-up — WIP, not deployed

Repository: AMASI-SA/AMASI-SA.
Branch: fix/operational-financial-followup-20261101. Draft PR #1677 targets the parent task, not production.
Parent: Draft PR #1675 at a8ffd5a81637bed8c4462f7bb4c2b059782cd520.
Resumed from verified follow-up head d3f5543f4b7280bd3a223743b4c9e539d86eaa19.
The PR head is the current checkpoint; it is not embedded self-referentially here.

## Current owner decision — 2026-11-01

Payroll begins on the operational cutover day itself, inclusive, using the Riyadh business date of started_at. It does NOT begin on the first of that month or on the following day. This supersedes the month-first requirement in earlier checkpoints. An eligible employee earns a full daily share on the first day even when startup occurs after midnight. Daily share = monthly salary / actual calendar month days. Employment start, unpaid leave, effective salary revisions and cumulative penny rounding still apply.

Example: SAR 3,000/month with 15 November startup earns SAR 100 on 15 November and SAR 1,600 through 30 November. Days 1–14 November are excluded. December uses 31 as its divisor.

Delivery after startup earns the actual driver's/carrier's fee regardless of order age. Newly approved supplier invoices likewise do not depend on order creation date. Those changes are inherited from #1675.

## Changed in this correction

- backend/operational_salary_start.py: both policy validation and preview require the exact cutover date. Wrong dates raise operational_salary_start_must_equal_cutover_day. Arabic preview text states same-day inclusion and exclusion of preceding days.
- backend/tests/test_operational_balance_salary_cutover_day.py: 18 new regressions for midmonth/late-night startup, UTC and negative offsets, month-end/leap-year denominators, rollover, later hire, exact-start accrual and idempotency.
- backend/tests/test_operational_balance_salary_start_mongo.py: concurrent approval/refresh now covers day 1 and day 15, yielding five real-Mongo payroll cases.
- .github/workflows/operational-followup-candidate-tests.yml: acceptance requires all five Mongo cases, with the same isolated localhost database and contents-read permissions.

Owner/opening review, source/session checks and once-only policy approval are preserved. Existing salary days, payments, cash and openings are not rewritten. A policy covering dates before cutover is not silently reinterpreted. Existing tenants without an approved policy retain their old behavior until reviewed activation; this correction does not silently migrate live balances. The owner's clarification specifies the rule, not whether an existing opening amount includes payroll for that day.

## Verification

The 752c69b1a54e0f90ae84fc5d2cb0d0b9cbe0eeb7 source archive was hash-verified against all 4,549 included Git blobs. GitHub comparison confirmed d3f5543f changes only this document; its current blob was verified as 54da31487d92e1050eaf31a956f1a547e4fd1e38. The local snapshot Git commit is not the remote SHA.

New tests before the fix: 17 failed, 1 passed. After the fix: 326 selected local tests passed, zero skips/failures/errors. AST parsing and git diff --check passed. This includes the 18 new cases and existing payroll, supplier, delivery and financial-report regressions. Local Python: 3.13.5; dependencies isolated under /mnt/data/payroll_cutover_20261101/testdeps.

Local command:
```bash
PYTHONPATH=/mnt/data/payroll_cutover_20261101/testdeps:backend:backend/tests python -B -m pytest --noconftest -q backend/tests/test_operational_balance_salary_cutover_day.py backend/tests/test_operational_balance_salary_start.py backend/tests/test_operational_balance_salary_consistency.py backend/tests/test_operational_balance_engine.py backend/tests/test_operational_balance_financial_position.py backend/tests/test_operational_balance_delivery_report_coverage.py backend/tests/test_operational_supplier_invoice_cutover.py backend/tests/test_operational_balance_sources.py backend/tests/test_operational_balance_source_fixes.py backend/tests/test_operational_balance_delivery_evidence.py --tb=short
```

Fresh combined backend CI and real Mongo acceptance for this correction are pending at checkpoint creation. Previous 931 tests plus 18 subtests belong to 752c69b1, run 41315032748, artifact 13085844813, not this source. Full historical evidence remains at this document's d3f5543f revision. Do not add overlapping local and CI counts or claim repository-wide/frontend/build/security acceptance.

## Remaining gates and boundaries

Owner payroll preview/approval UI and real opening-coverage review; operational driver bank/POS approval writer and verified report linkage; complete pre-start COD forecasts; consistent profit-period coverage; integration with the exact published cost prerequisite and updated dependencies; combined frontend/security/build/browser/print/export verification. Unsupported financial totals remain unavailable.

Last prerequisite observation: #1676 merged at ffa145f22dbf4167649736336083d0d16f9fed27, which is not live v5 publication proof. No fresh production verification was performed in this correction. #1675 previously had production-base conflicts; no integration/rebase was performed here.

Production changed: NO. Live salary approval, bank cash, openings, ledger, /app, release intent, lease, deployment, parent branch: untouched. Keep #1677 Draft and unmerged.

Next safe action: verify the remote head and fresh candidate CI, append results to this record, then continue the owner interface and other remaining gates. Do not reintroduce month-first accrual. Issue #1006 is full; use this record and #1677 for continuity.
