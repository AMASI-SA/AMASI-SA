# Courier live receipt recovery — 2026-11-18

## Current state: WIP, not a release candidate

Task: preserve the verified native result for one explicitly selected courier order when the subsequent financial aggregate fails. Retain enough sanitized timing evidence to diagnose the observed live source-read timeout.

Repository: AMASI-SA/AMASI-SA
Task branch: codex/courier-live-receipt-20261118
Source preparation base: b92d3eb8d12920663a052744c9c0d2773a785e1a (successor PR #1972).
The current immutable intent is unchanged. The successor deployment has its own active lease and must not be altered or reused by this task. Do not freeze another intent until its published base is independently established.

## Verified prior milestone

PR #1970 was normally merged as d7c9ed77137bed0948f4d32a34c6bf571d75e6f8 and published once as Emergent version 4002d88. The actual adapter, prepare, prepublish, and unmodified production verification passed. The verifier returned three successful checks (594 public requests per check), source d9964df9f07cee22806202311712d92e6bd717ba, release rg5-6ebdaece1a49b074f899db8024e68a790da8cc641b66206a2e4abd18d4be6d9b, and closed its matching lease.

Production changed: YES, for that accepted PR #1970 release. The live business reconciliation remains FAILED. An authorized selected-order attempt claimed its native retry slot but did not save a verified delivery transition. Aggregate refresh returned operational_source_setup_incomplete / operational_source_read_timeout for unified_orders and kept the previous snapshot. No pending-count or pending-money reduction is claimed.

The native receipt is currently lost if the aggregate raises. Browser console logging passes timing arrays as separate objects, which string-only diagnostic readers render as Array(n). These are reproduced control-flow gaps, not evidence that the underlying provider or database problem has been fixed.

## Change contract

- Keep a completed native receipt only for the explicitly requested order on the existing 409 source failure or 408 refresh timeout response.
- Preserve original HTTP status, failure code/message and stage timings.
- Recheck authorization/session scope before returning account-specific receipt data.
- Reconstruct a bounded allowlisted receipt; omit provider payloads, private messages and other orders.
- Show native proof outcome separately from the failed aggregate, never claiming balances were recalculated.
- Serialize only the existing allowlisted, bounded timing records into a diagnostic string.
- Preserve cooldowns, source membership, financial evidence rules, timeouts, leases, cancellation, and all successful-path behavior.
- Do not change the source collector, indexes or budgets without live measurement evidence.

## Ownership and verification

Root owns frontend integration, timing serialization, related tests and this status document.
The backend worker owns the demand-refresh route and focused backend route regressions.
An independent reviewer must assess the integrated patch.

Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1977. Initial durable checkpoint: 5778ec6db0e0e42e0098c3753789d0a59b14b55d. Issue #1006 is full, so this task status and PR hold its continuation record.

Frontend regression evidence: the first two targeted suites failed for the intended missing behavior (12 failed, 83 passed, exit 1). After the bounded receipt and message changes, all 95 passed. Independent review found another receipt-loss path in the real financial page: the follow-up report GET could fail after the native result had returned. Three new real-page regressions reproduced this (3 failed, exit 1); the page now retains the existing snapshot when a freshly scoped failure receipt is available. Successful refreshes and unscoped failures retain their original read path.

Fresh integrated frontend command: `CI=true python3 scripts/frontend_release_toolchain.py exec -- yarn --cwd frontend test --watchAll=false --runInBand --runTestsByPath src/pages/operational/OperationalFinancialPosition.test.jsx src/pages/operational/visibleOperationalRead.test.js src/pages/operational/PendingOrdersDialog.test.jsx` — 202 passed, 3 suites, exit 0. Governed Node 22.23.2 / Yarn 1.22.22. `git diff --check` passed before the last integration slice; rerun at the combined checkpoint.

The backend regression module independently reproduced the missing receipt and missing failure-path authorization check (10 failed, 18 passed, exit 1). Its route implementation and bounded post-failure authorization tests are still in progress. These incomplete backend files are not part of the frontend checkpoint. No production change by this follow-on, no release intent frozen, no acceptance or merge claim.

Required remaining evidence: complete backend tests including bounded authorization and disconnects; independent integrated review; proportionate repository CI and release gates before any merge or deployment.

## Remaining work / next safe action

1. Complete and verify the bounded failure receipt contract.
2. Investigate native acquisition using authorized existing diagnostic surfaces. The app reports the Salla connection active; cached activity has no usable history. Do not substitute a manual delivery assertion.
3. Obtain the actual scalar source-stage timings or authorized live query evidence before modifying order read performance. Existing owner-leading indexes and root projection already exist.
4. Preserve this WIP and tests on the dedicated branch. Keep private order data outside GitHub.
5. Respect the separate PR #1972 lease; do not abort, reuse or publish over it.

