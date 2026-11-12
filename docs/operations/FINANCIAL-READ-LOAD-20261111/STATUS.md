# Financial read load — bounded first slice

## Change contract

- Task: `FINANCIAL-READ-LOAD-20261111`.
- Release branch: `codex/financial-read-load-publish-20261112` in `AMASI-SA/AMASI-SA`. Earlier branches remain preserved: `codex/financial-read-load-release-20261112` at `dc764d7a27f9eac87f3115ded667e10041216998` / PR #1889 and `codex/financial-read-load-20261111` at `050bd14ef971888e0a57b11c528cd7a10caa672a` / PR #1888.
- Original implementation base: `245a0beccbdbc2585a3ec2214e808eeef3734b57`. Current release base, verified by explicit fetch: `e0f00c6ec47536aaaa28ac3fa580f3d6abc02c96` (the normal merge of PR #1891's reviewed intent-only B).
- User requires completion of operational accounting without repeating the load introduced by the earlier financial release.
- Observable first slice: remove redundant incoming-COD history scans and stop fetching the closed audit panel. The user's subsequent explicit direction on 2026-11-12 (Asia/Riyadh) supersedes the original retained cadence: reports load on opening or explicit refresh, with no periodic context/report polling.
- Invariants: identical monetary outputs, input immutability, validation/authorization/idempotency preserved; no source sync, new periodic jobs, changed shipping eligibility, historical processing, financial writes or restored full-report-per-event capture.
- Preserve existing `asyncio.to_thread(reconcile, ...)`, dashboard/Snapchat releases #1883/#1884, preparation creation exclusion and concurrent Google/Android work.
- **User explicitly authorized merge and publication.** Both `050bd14e` and the subsequent source `dc764d7a` passed all 11 exact-head workflows. A fresh source/build/intent is being prepared above production `e0f00c6e` to preserve the later Google/store-courier releases. This financial task has not changed deployed Production, acquired a lease or published.

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

## First UI checkpoint and verification (retained cadence superseded)

The first checkpoint, `cdd9170dd786ef007c6d6d7b10871ee6a9bd84fb`, implemented:

- Fetches and renders audit rows only when that disclosure is opened; its manual refresh and errors are independent of the report.
- Retained the existing 30-second visible context/report cadence, paused reads while hidden/offline, and resumed one read when visibility/connectivity returned. **This cadence is superseded by the subsequent user direction below.**
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

Independent read-only review of the integrated runtime diff found no material blocker. Its separate focused load + real CustomerWalletBalance component run passed **19 tests / 2 suites**, exit 0 (3.949 seconds). Fourteen overlap with the root load suite; do not add these counts as unique tests. It confirmed auth/scope fences, serialized pending reads, audit independence, unchanged write validation and preserved reconciliation offload. Admission is per mounted hook, not global across tabs/remounts; old unmounted requests may finish but cannot apply their results.

This proves request-count/response-order behavior in component tests. No browser/production latency acceptance or cross-tab deduplication is claimed. The full report, cost/purchase children and context server paths still need the later bounded-read architecture.

## Demand-only follow-up — 2026-11-12 (Asia/Riyadh)

The user approved running reports only after their page is opened. This supersedes the first checkpoint's visible 30-second cadence, not financial recording or reconciliation.

Contract for this follow-up:

- Read context and the displayed report on initial opening; remove both periodic timers. Ordinary focus, visibility and connectivity changes do not request fresh data when no explicit or initial read is outstanding.
- Retain one queued initial/explicit read while hidden/offline. Preserve same-mounted-scope single-flight and generation/actor/owner response fencing.
- Arabic **تحديث الأرصدة** first rechecks context, phase and permissions, then reads the main report under the returned scope. Server authorization and existing pre-submit validation remain authoritative.
- Preserve report refresh after relevant same-page saved changes and on reopening the report. Unselected report content must not launch report reads.
- Keep section refreshes independent: the existing cost button, demand-only audit opening/refresh, and a new report-mode-only **تحديث المشتريات**. Purchases reports no longer refresh on window focus; purchase/payment entry behavior stays intact.
- The standalone financial-position page already loads on mount/manual refresh and has no timer. Its existing as-of output and scope checks are preserved.

Root reproduced the unwanted purchases-report focus read before the change: the narrow regression returned **1 failed / 2 passed**, as intended. After the reports-only focus guard and manual button, the complete InventoryPurchases suite returned **43 passed**, exit 0, including pending-read admission, fresh returned data and unchanged purchase/payment entry behavior. The 39 unselected cases in the initial narrow reproduction are included in the passing full suite; none were removed or skipped in that complete run.

The frontend executor's final three-suite run passed **113 tests** (62 balances, 22 focused read-load, 29 salary-start). Root independently tested the integrated four-suite tree, including real InventoryPurchases behavior: **156 passed / 4 suites**, zero failed/pending, exit 0, 9.715 seconds. Sanitized current evidence: `ui-checks.json`. The existing cadence test was updated to the user's explicit demand-only contract; financial assertions and the other 61 existing balances cases remain intact.

The request-count checks advance ten minutes of visible idle time without another context/report request, exercise hidden/offline queued demand, manual refresh ordering, repeated clicks, report-tab reopening, mutations during pending context, revoked report permission, phase changes, independent audit errors and stale actor/revision responses. A review-found ordering case was fixed: report demand is gated by both the selected tab and completion of context verification, so a save or tab reopening cannot read the report from an older context while an explicit context refresh is pending. The report reader stays mounted across internal tab changes to serialize any pending read. Its prefixed React key avoids collision with financial entry children.

```sh
cd frontend
CI=true node node_modules/react-scripts/bin/react-scripts.js test \
  --watch=false --runInBand --resetMocks=false \
  --testMatch '**/src/pages/operational/OperationalBalances*.test.jsx' \
              '**/src/pages/operational/SalaryStart.test.jsx' \
              '**/src/pages/operational/InventoryPurchases.test.jsx'
```

Final independent review found **no material blocker** in the completed integrated diff. Its separate read-load plus real InventoryPurchases execution passed **65 tests / 2 suites**, exit 0, 4.230 seconds, with no warnings or skips. Those 65 cases are included in root's 156, not additional unique cases. New exact-head CI is pending at this checkpoint; the previous checkpoint's 11 successful workflows do not verify these new edits. The backend and its recorded monetary-parity/integration evidence are unchanged in this follow-up; no live data or accounting writes were used for verification.

Visibility boundary: the main context/report/audit hooks gate new reads while hidden/offline. Existing cost/purchase and standalone financial-position components do not poll, but a one-time mount read or an already sent request may finish after visibility changes. This follow-up does not claim HTTP cancellation, cross-tab deduplication, zero hidden-tab network traffic, or whole-platform latency acceptance.

## Remaining low-load design boundaries

- Both the first checkpoint and the demand-only implementation checkpoint (`050bd14e`) completed all 11 returned exact-head CI workflows successfully. A release continuation above the newer production base requires fresh source/intent checks. Preserve demand-only display reads and existing post-mutation refresh.
- A durable next slice should serve a dated, prepared report tied to owner and all input versions, rather than rebuild it for each reader. Recheck permissions per request; cached display data never authorizes a financial write.
- Cost/source changes and day-based accrual must invalidate the relevant snapshot. `as_of` changing by itself is not evidence of an economic change; current worker increments state revision on routine rounds, so revision-only caching is insufficient.
- Use server pagination for history. Ten displayed rows do not bound work when the server still normalizes/hashes the full history.
- Before restoring after-balance capture, maintain transaction-local indexes and each real event's prefix balance, rebuilt on CAS retry. Preserve evidence validation and do not stamp the final batch balance on earlier events.
- Limit background work per owner and prevent duplicate jobs across processes. Background/thread placement alone does not reduce CPU work.
- Required pre-release proof for broader restoration: monetary parity, invalidation/permission changes, retry/idempotency, and concurrent ordinary request latency while accounting work runs. No live latency or whole-platform acceptance has been measured here.

## Authorized release continuation — 2026-11-12 (Asia/Riyadh)

The user explicitly requested **merge and publish**. While preparing this release, PR #1887 completed its own gates and merged as production `803e87f72d9661f9b961587bbc0cededfd1f3c90`, tree `ac5225a4cb70b150ee8383305fb5b5da3c3a8a25`. Its source/intent pair is `b01341f95f1a1caf3a8741de09f52cb52f6536d9` / `b237fca03fb22e627ab5451b2c14c0f1a17f42b5`. Its deployed acceptance is owned by that release and is not inferred from the merge.

Independent release review confirmed exactly three financial runtime files, unchanged financial authority and all original implementation gates. The concurrent production update changes none of those three files, `frontend/package.json` or `frontend/yarn.lock`.

To preserve protocol v5 intent ancestry, this continuation starts a fresh release branch directly from the new production base and applies the exact reviewed financial diff `245a0be..050bd14e`. A read-only merge-tree/history inspection proved that merging the new base into the older branch introduces an intent-touching merge into `J..A`, which the unchanged v5 guard correctly rejects. Neither guard nor old source history was modified or force-pushed. Original PR #1888/branch remain a recoverable implementation checkpoint; a linked release PR carries the new A/B handoff.

Local pinned toolchain is warm-verified: Node 22.23.2 / Yarn 1.22.22. A private copy of the existing locked dependencies replaces only this task's ignored dependency symlink; manifests match exactly. Fresh CI and the actual shared deployment adapter retain their frozen-install requirements. No shared `/app` write or rehearsal may start before fresh official guard status proves inactive, and another release's lease must remain with its owner.

Next: verify the new source's exact financial file parity and untouched production content, run the governed reproducible build and fresh exact-A CI, independently compare/freeze intent-only B, complete exact-B gates, then serialize the shared adapter and one authorized publication after the previous release. Verify newer explicit deployment success, three official production probes and final inactive guard. No financial reprocessing, correction, preparation change or paid Emergent chat is authorized by this release.

## Resumed publication after session interruption — 2026-11-12

The original release source `dc764d7a27f9eac87f3115ded667e10041216998` remains immutable and recoverable. All 11 returned exact-head workflows completed successfully, including Operational Balance Web, Operational Stock Eligibility and Mezan Release Readiness. Its governed local build log reaches successful completion, but that earlier artifact is not reused for a new source.

During the interrupted authentication handoff, production advanced through PR #1890 and PR #1891. Fresh Git fetch and independent connector review agree on `e0f00c6ec47536aaaa28ac3fa580f3d6abc02c96`, tree `3949c7392232f21a46fa2cd0c87721a79d774116`. PR #1891 records publication `0cdea89`, three successful official probes and subsequent inactive guard. The Emergent UI independently shows the newer Nov 12, 05:18 AM publication. These records do not replace the fresh shared guard check required before this task changes `/app`.

The prior local worktree's Git common directory was no longer available after session resumption. A separate complete clone was created from the authorized repository; no prior worktree, branch, dependency directory or foreign source was repaired or deleted. Explicit fetch confirms both earlier financial checkpoints. This new branch begins directly at current production and applies the exact `803e87f7..dc764d7a` financial patch. The 18 intervening production files do not overlap the three financial runtime files, package/lock, AGENTS or release adapter. All financial patch blobs except this STATUS must remain identical to the reviewed source. The inherited intent file stays byte-identical to current production, with no intent-touching history in the new source range.

Independent read-only release review approved this source-transition strategy. Issue #1006's body was older than PR #1891; its all-comments connector failed with `Transport closed`, so no unobserved comment or shared ownership is inferred. A new ledger checkpoint records the current financial continuation without replacing other tasks' entries.

The main Emergent account is signed in, but a fresh code-server tab still shows its separate password login. The secure authentication request was interrupted; no credentials were inspected or retried, no shared source changed, and no financial lease was acquired. Complete all available source, build, intent, review and CI gates before resuming that authenticated publication boundary. User authorization for merge/publication persists; authentication is a separate access requirement.

Next: verify exact source parity and unchanged inherited intent, run this new source's governed reproducible build and exact-head CI, independently compare the local and CI candidate, freeze intent-only B, complete exact-B checks, and perform the authorized normal merge. Shared adapter/prepare/prepublish/publication require authenticated terminal access and fresh inactive guard. No deployed-financial success is claimed until the intended release passes the official three probes and final inactive check.

## Resume

Read the latest Issue #1006 entry and this STATUS; fetch current production and the exact release continuation checkpoint before further changes. Root owns Git/integration and publication. Implementation/review agents are complete; further review is read-only. Preserve the old PR #1888 checkpoint and all frozen release pairs. Complete the authorized release through the current v5 gates; do not revive the historical finance bundle from the old base.
