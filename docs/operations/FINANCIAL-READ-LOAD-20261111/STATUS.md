# Financial read load — bounded first slice

## Change contract

- Task: `FINANCIAL-READ-LOAD-20261111`.
- Branch: `codex/financial-read-load-20261111` in `AMASI-SA/AMASI-SA`.
- Base verified by explicit fetch: `245a0beccbdbc2585a3ec2214e808eeef3734b57`.
- User requires completion of operational accounting without repeating the load introduced by the earlier financial release.
- Observable first slice: remove redundant incoming-COD history scans; stop fetching the closed audit panel; bound the existing visible-page polling requests without changing their cadence.
- Invariants: identical monetary outputs, input immutability, validation/authorization/idempotency preserved; no source sync, new periodic jobs, changed shipping eligibility, historical processing, financial writes or restored full-report-per-event capture.
- Preserve existing `asyncio.to_thread(reconcile, ...)`, dashboard/Snapchat releases #1883/#1884, preparation creation exclusion and concurrent Google/Android work.
- **Implemented first slice; local verification passed; remote CI/release acceptance pending. Production changed: no.** No release intent, lease, merge or publication.

## Verified mechanisms, not incident attribution

The code review at the base found:

1. OperationalBalances requests context, report and the entire audit every 30 seconds even when audit is collapsed. Each endpoint independently calls full-state read. FinancialPosition itself does not poll.
2. Full-state read unpacks archived engine audit pages and validates their hashes. Context does not have a lightweight metadata-only path.
3. Report scans all movements/allocations per COD claim. Other report copying/projections remain substantial.
4. FinancialPosition reads live cost inputs from 12 source collections and builds the report in its request; its cache cannot safely be keyed only by operational revision.
5. The historical rolled-back prepaid audit hook invoked a full report per newly captured charge. PR #1846 avoids that particular full report for ordinary charges but still scans history per event. Restoring it wholesale would not prove bounded batch work.
6. Reconciliation already uses a worker child process and thread offload. Those mechanisms preserve responsiveness boundaries but do not remove shared CPU/database work.

These mechanisms establish avoidable work. They do not establish what percentage of the historical production incident each caused. No live production load test or database access was performed in this task.

## Backend implementation and evidence

Only the COD-index hunk from PR #1844 was applied to the current service. Incoming allocations are accumulated once while preserving each claim's exact movement/allocation addition order. Outgoing allocations, duplicates, unknown/missing claims, overpayment behavior, decimal formatting and all other report logic retain their existing semantics.

- `backend/operational_balance_service.py`: one pass instead of one pass per claim; no persistent cache or state/schema change.
- `backend/tests/test_operational_balance_cod_projection_performance.py`: recovered synthetic public-report regression from exact #1844 source.
- `scripts/benchmark_financial_report_reads.py`: synthetic-only before/after harness; prevents network connections, checks input immutability, full report hash and independent bank/COD arithmetic.

Regression on the unchanged current service: **1 failed / 2 passed**, as intended. The movement-pass count grew from 17 for 10 claims to 107 for 100 claims. After the patch the focused regression passed.

Fresh local related pure-contract suites: **114 passed**, exit 0, covering COD scan bound, closure, financial position, driver review reporting, provider components, engine/engine fixes and prepaid position.

Fresh local real-Mongo suites: **13 passed**, exit 0, covering COD flow and provider net authority on a dedicated temporary loopback replica set and disposable synthetic databases. The server was terminated and temporary data cleaned afterward. Local Mongo 7/Python 3.12 differs from required CI Mongo 8/Python 3.11; this is not a waiver of CI.

An initial integration selection was attempted without starting that local Mongo and was interrupted after connection failures (exit 130). No successful integration claim is based on that attempt. The 13 passing cases are the subsequent correctly isolated run; no cases were skipped or assertions weakened.

### Controlled report measurement

Same current dependencies/interpreter, 5,000 synthetic movements, 7 warmed samples per combination. Baseline service was saved directly from the base commit; candidate changes only the COD hunk. Complete report hashes match before/after and inputs are unchanged. Raw results: `report-benchmarks.json`.

| COD claims | Baseline median | Candidate median | Median reduction |
| --- | --- | --- | --- |
|100|188.273ms|107.025ms|43.15%|
|1000|939.012ms|156.961ms|83.28%|

This measures one synchronous report on synthetic data. It is **not whole-application or production improvement**; candidate report work can still delay the event loop. No promise of zero overhead is made.

Reproduction for focused pure checks:

```sh
PYTHONPATH=backend:backend/tests PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
python -B -m pytest --noconftest -q \
backend/tests/test_operational_balance_cod_projection_performance.py \
backend/tests/test_operational_balance_report_closure.py \
backend/tests/test_operational_balance_financial_position.py \
backend/tests/test_operational_balance_driver_review_report.py \
backend/tests/test_operational_balance_provider_components.py \
backend/tests/test_operational_balance_engine.py \
backend/tests/test_operational_balance_engine_fixes.py \
backend/tests/test_operational_balance_prepaid_position.py --tb=short
```

For comparison, save base `backend/operational_balance_service.py` outside the repository and run the benchmark with `--service-file <saved-base> --claims 100 --repeat 7` and candidate without `--service-file`; repeat with `--claims 1000`. Required integration uses a fresh dedicated loopback replica set; never point a test at live data.

## UI implementation and integrated verification

`frontend/src/pages/operational/OperationalBalances.jsx` now:

- Fetches and renders audit rows only when that disclosure is opened; its manual refresh and errors are independent of the report.
- Retains the existing 30-second visible context/report cadence, pauses reads while hidden/offline, and resumes one read when visibility/connectivity returns.
- Allows one pending read per mounted scope; an explicit mutation refresh invalidates the older response and queues one replacement. Periodic ticks do not accumulate work.
- Remounts content on auth actor/owner/role change, rejecting old scope/context responses.
- Preserves the report date and existing financial mutation callbacks. No API cache, server calculation or validation contract was added or removed.

New focused file: `OperationalBalances.readLoad.test.jsx`, 14 cases for closed/open audit request counts, independent audit failure, hidden/offline entry/polling, slow context/report reads, mutation supersession, audit close/reopen and actor/session changes.

The executor reproduced the missing behaviors against the unchanged source before editing. One timer assertion was corrected to settle each existing 30-second tick separately; no behavior expectation was weakened.

Both the executor and root independently ran the complete selected frontend suites on the integrated tree: **105 passed / 3 suites, exit 0** (62 existing balances, 29 salary-start, 14 new load cases), without skipped tests. Root run took 13.003 seconds. Local Node 24.22.1/Jest 27.5.1/React 19.2.8; required CI uses its pinned Node 22 toolchain.

```sh
cd frontend
CI=true node node_modules/react-scripts/bin/react-scripts.js test \
  --watch=false --runInBand --resetMocks=false \
  --testMatch '**/src/pages/operational/OperationalBalances*.test.jsx' \
              '**/src/pages/operational/SalaryStart.test.jsx'
```

This proves request-count/response-order behavior in component tests. No browser/production latency acceptance or cross-tab deduplication is claimed. The full report, cost/purchase children and context server paths still need the later bounded-read architecture.

## Remaining low-load design boundaries

- Finish independent patch review and exact-head CI before any release preparation. Keep the visible 30-second cadence and existing post-mutation refresh.
- A durable next slice should serve a dated, prepared report tied to owner and all input versions, rather than rebuild it for each reader. Recheck permissions per request; cached display data never authorizes a financial write.
- Cost/source changes and day-based accrual must invalidate the relevant snapshot. `as_of` changing by itself is not evidence of an economic change; current worker increments state revision on routine rounds, so revision-only caching is insufficient.
- Use server pagination for history. Ten displayed rows do not bound work when the server still normalizes/hashes the full history.
- Before restoring after-balance capture, maintain transaction-local indexes and each real event's prefix balance, rebuilt on CAS retry. Preserve evidence validation and do not stamp the final batch balance on earlier events.
- Limit background work per owner and prevent duplicate jobs across processes. Background/thread placement alone does not reduce CPU work.
- Required pre-release proof for broader restoration: monetary parity, invalidation/permission changes, retry/idempotency, and concurrent ordinary request latency while accounting work runs. No live latency or whole-platform acceptance has been measured here.

## Resume

Read the latest Issue #1006 entry and this STATUS; fetch current production and the exact task checkpoint before further changes. Root owns Git/integration. The frontend executor owns only OperationalBalances and focused tests; backend/source/release surfaces are not delegated. Do not deploy before outstanding gates or revive the historical finance bundle from the old base.
