# P01 report isolation — final scoped acceptance

PR [#1110](https://github.com/AMASI-SA/AMASI-SA/pull/1110), draft/open/unmerged.

- Source A: `163874f5ffcab10a5f3dc883a1c2acd56c414bf9`.
- Intent-only direct child B: `3ef3cdf23d9f1f7f1d4f3dea4fd56c23f169b045`.
- Production base J: `ebf92e6606a810a1e3f0a0e2026984a5482f71bc`.
- Runtime release identity: `rg5-0cc93e88e138b1b236ceab6d30b0f53205c53dce8126dd35e5653e4961ef1522`.
- Previous #1109 A `f1bb92adeb3a835f032fb4cd7ddbf6e9447331fd` and B `3d73591d15472df39922dc28270374934c1f8d88` unchanged. This branch begins from old A, never old B; retains all four accepted fixes and write control. Do not deploy candidates sequentially.
- P01 IN_PROGRESS; P02 LOCKED; Production unchanged by this task. No production lease, merge, publication, financial write or index/configuration change.

## Sentinel results

| Case | Actual setup and result |
|---|---|
| A | New isolated Mongo: bank current_balance=987654321; approved opening reference removed. Current and Aug31 reports show needs_opening_balance, no numeric assets/liabilities/totals. UI explicitly says unavailable is not approved zero. PASS. |
| B | Same owner/bank/main, three legacy debit legs 1234567 each at 2019-12-31, 2026-09-04 and 2026-09-10; no operation_id and no legacy_orphan flag. Complete current and three historical responses exactly equal before/after. No legacy group in journal. PASS. |
| C | Explicit synthetic approved opening 1000 + real-service sale 115 + refund/payment journals; only this opening batch and eligible MZ2 activity included. Legacy control test reproduces987654321 in old reader while MZ2 blocks. PASS. |
| D | Real service accrual Aug31 and approved payments 40 Sep2 / 75 Sep5; UI 115→75→0→115Aug31 re-read; bank 1000→960→885. PASS. |
| E | Real Mongo + ASGI employee reads persisted owner's scope across 3 endpoints; forged role/owner/operation queries cannot widen scope (422), revoked/disabled actors 403. Frontend navigation cannot fetch without permission. PASS. |

## Actual Preview

[Reports](https://salla-analytics.preview.emergentagent.com/integrations-v2?workspace=financial&page=journals-reports)
uses exact source A in `/app/.worktrees/p01-report-final-preview-20260920`.
Database `mz2_report_isolation_preview_20260920`, local replica set `p01acceptance`.
No existing acceptance database was used for new financial writes. Previous ledger,
accounts, refunds, payments and settings collection counts and SHA256 hashes match
before/after; included in [raw evidence](evidence/report-isolation-20260920/preview-acceptance.json).
Runtime boundary and complete source manifest verified; external network denied and
automatic startup workers disabled. Authentication user was copied privately to the
new synthetic database; no credential or real customer record is in this evidence.

Synthetic opening/cutover/tax fixture is test-only. Its opening contract was explicitly
seeded for this reader test; this is not acceptance of a Production opening approval
workflow. The sale fixture date is 2020-01-02; refund/payment dates are 2026 August/September.
The UI dates below are Riyadh end-of-day, independent of posting insertion timestamps.

| Report date | Bank | Provider receivable | Customer refund payable | Sales VAT payable | Assets | Liabilities |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-31 | 1000.0 | 115.0 | 115.0 | 0.0 | 1115.0 | 115.0 |
| 2026-09-02 | 960.0 | 115.0 | 75.0 | 0.0 | 1075.0 | 75.0 |
| 2026-09-05 | 885.0 | 115.0 | 0.0 | 0.0 | 1000.0 | 0.0 |
| Current | 885.0 | 115.0 | 0.0 | 0.0 | 1000.0 | 0.0 |

August re-read remains 115 after September payments and legacy injection. UI trial
balance and journals contain only eligible rows; current trial debit=credit=1345.
The refund accrual debits revenue 100 and VAT 15 and credits customer refund payable 115;
payments debit payable 40/75 and credit bank 40/75, without new revenue/VAT effects.

| Group | Type | Accounting instant |
|---|---|---|
| `54462bd7-928c-4441-84b9-8d20c386a17f` | opening_balance | 2020-01-01T00:00:00Z |
| `660013ad-c9ae-4a08-8877-f8d623ff55c5` | customer_refund_payment | 2026-09-05T09:00:00.000000+00:00 |
| `a0a08b65-5ca3-46d7-b9c0-1de1785bf198` | customer_refund_payment | 2026-09-02T09:00:00.000000+00:00 |
| `bfff7414-391e-47ca-b4ba-9e57baf4243d` | customer_refund_due | 2026-08-31T20:30:00.000000+00:00 |
| `d36f3e39-a91f-4773-809f-ca4166ea0345` | bnpl_sale | 2020-01-02T12:00:00+00:00 |

## Verification

- 16 new real-Mongo report-isolation/consumer tests PASS (temporary unique databases).
- 5 affected refund entitlement tests PASS; 17 accounting/register/date unit tests PASS.
- 24 focused frontend tests PASS: 17 report/register/pages + 5 actual Radix dialog + 2 actual router navigation/permission. Test-local package-export/encoding adapters support the existing Jest version; production dependencies unchanged.
- All 9 workflows SUCCESS for A and all 9 for B, recorded in [ci.json](evidence/report-isolation-20260920/ci.json).
- A MZ2 [35519227471](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35519227471), A release [35519227483](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35519227483).
- B MZ2 [35519516791](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35519516791), B release/clean clone [35519516789](https://github.com/AMASI-SA/AMASI-SA/actions/runs/35519516789).
- Clean-clone job 106101174410 passed Host Node 20 outer frozen install, generated-input absence, no-Git package adapter, governed Node 22 dual reproducible build, artifact/proof/identity verification, isolatedBackend without siblingFrontend, package boundaries and actual JSON runtime metadata route. This is a package rehearsal, not a Production deployment.
- Intent artifact 10608067086 SHA256 `a374f0c1e2c67e77bd6cae12c18253b693be518fa144d4aa74c040b885eb6b81`.
- Clean-clone artifact 10607858097 SHA256 `231cc7b6a8876a24e6a0e1da3955dbbc4ba4819e48a12675c38e8b4cd78634cb`; sanitized files retained under evidence/report-isolation-20260920/clean-clone.
- Initial test expectation mismatch was corrected to qualified account key; final Mongo suite passes. Additional frontend harness load errors were resolved before source A freeze. No failed test is waived.

## Consumer scope and changed files

The [reader inventory](P01-REPORT-ISOLATION-20260920.md#MZ2-consumer-inventory)
records endpoint/frontend/old reader/new boundary. Additional affected consumers
were trial balance, journal landing, home balances, refund journal, and settlement
journal detail. All now use the same fixed owner/operation/opening/economic-date
boundary. Legacy financial position, trial balance, transactions and migration
reconciliation endpoints/pages remain unchanged; MZ2 report navigation no longer
links to those hybrid financial readers.

Malformed, reversed, unsupported, unbalanced groups and contradictory zero-opening
evidence block the report. More than 10000 scoped legs block explicitly; pagination
is not introduced. Approved zero evidence must identify account and bind to the
exact opening group/date; later activity never supplies an implicit opening zero.

Source delta from accepted old A (20 files):

- `.github/workflows/mz2-accounting-module.yml`
- `backend/accounting_module_ledger.py`
- `backend/accounting_module_status_routes.py`
- `backend/accounting_mz2_reports.py`
- `backend/accounting_refund_entitlements.py`
- `backend/accounting_settlement_register_routes.py`
- `backend/financial_provider_apps.py`
- `backend/tests/mz2_report_fixtures.py`
- `backend/tests/test_mz2_refund_entitlements.py`
- `backend/tests/test_mz2_report_isolation.py`
- `backend/tests/test_mz2_settlement_register_p01.py`
- `docs/operations/MZ2-FIN-CUTOVER-001/P01-REPORT-ISOLATION-20260920.md`
- `docs/operations/MZ2-FIN-CUTOVER-001/STATUS.json`
- `frontend/src/pages/accounting/AccountingReports.jsx`
- `frontend/src/pages/accounting/AccountingReports.test.jsx`
- `frontend/src/pages/accounting/AccountingWorkflowPages.jsx`
- `frontend/src/pages/accounting/AccountingWorkspace.jsx`
- `frontend/src/pages/accounting/AccountingWorkspaceReports.test.jsx`
- `frontend/src/pages/accounting/SettlementJournalDialog.jsx`
- `frontend/src/pages/accounting/SettlementJournalDialog.test.jsx`

B changes only `release/release-intent-v5.json`. This evidence branch is not a deployment
candidate and must not be appended to frozen B for release.

## Independent remaining launch gates

1. Production database identity and read-only evidence of transactions, uniqueness
   indexes, backup policy/latest successful backup and an actual isolated restore
   test remain NOT VERIFIED. Needed access: Emergent project's Production database/
   connection and backup administration page, or linked MongoDB provider Cluster
   and Backups/Restore pages explicitly mapped to mezansalla.com. Provider/cluster
   name is unconfirmed; do not presume Atlas or infer it from Preview or /app.
2. Owner approval of tax rate and effective date, cutover date/time/timezone,
   reconciliation/evidence and approved opening batch/account coverage, operating
   owner and permissions, and activation/write-pause/drain/replay plan. Test values
   are not Production configuration. Taxable advances remain held for accountant review.
3. Independent review/authorization to merge and separately publish the exact new
   candidate, followed by v5 production gates and deployed identity proof. No such
   authorization has been exercised.

Adjacent shared compute_balance reads in settlement/refund/advance WRITE validation
were observed but not changed or certified by this requested reader-isolation scope.
Their independent review remains distinct from this report acceptance; do not claim
that this scoped result certifies every legacy-linked financial path.

Final runtime source/boundary verification PASS; Production guard active=false; own Preview reservation released.
