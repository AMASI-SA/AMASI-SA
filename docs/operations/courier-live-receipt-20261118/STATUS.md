# Courier live receipt recovery — 2026-11-18

## Current state: source implementation complete; awaiting CI and release validation

Task: preserve the verified native result for one explicitly selected courier order when the subsequent financial aggregate fails. Retain enough sanitized timing evidence to diagnose the observed live source-read timeout.

Repository: AMASI-SA/AMASI-SA
Task branch: codex/courier-live-receipt-v2-20261118
Source preparation base: b009ee50a6b932368be8ad104c8c1b75e584b4e2 (Android release PR #1974).
The current base's immutable intent is inherited unchanged. PR #1972 completed its official three-probe verification and closed its lease; PR #1974 subsequently merged and owns its separate publication. Do not alter or reuse another owner's lease. Do not freeze another intent until its published base is independently established.

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

Frontend WIP was preserved as 7b9efd7cb591154df383163e3bd0a0856b60f98a. The final frontend slice also distinguishes two fixed native failure reasons: access denied from a structured Salla 403 without reauthentication, and a typed HTTP timeout. Two additional regression cases failed against the earlier implementation and passed after the change. The same complete three-suite command now passes 204 tests, exit 0.

The backend regression module independently reproduced missing receipt and failure-path authorization behavior; final replay against the immutable original route produced 13 intended failures and 22 passes. The implementation retains only a fully returned exact-target receipt. Its optional post-failure reports-scope and actor/owner check has a separate one-second ceiling using asyncio and PyMongo CSOT. An unavailable check preserves the original failure without receipt; true 401/403 and cancellation propagate. Disconnects are checked before and after. The 85-second financial work deadline remains unchanged; this bounded diagnostic authorization tail cannot restart financial work.

The adapter only classifies the two new reasons from structured exception fields. It preserves unavailable/deferred statuses, claim limits, cooldowns, continuation, evidence verification and CAS. The adapter regression first reproduced four intended failures, then passed its 14 selected cases including cancellation. No provider messages, payloads or credential material enter the receipt.

Root's fresh integrated backend command used the dedicated venv and five affected modules: failure receipt, demand-refresh route, demand-refresh lifetime, targeted acquisition and acquisition progress. Result: 100 passed, 2 explicitly deselected existing real-Mongo cases, exit 0. An earlier full selection produced 100 passes and those two connection failures at the missing localhost test service. Attempting the existing official Mongo 8.0.12 binary locally failed before server startup on its tcmalloc CPU topology check. This environment limitation is not a product pass or a waived test; the full real-Mongo CI gate remains mandatory.

Independent integrated source review found no remaining blocking findings after both the second-GET receipt loss and unbounded reauthorization were corrected. Review executed no tests. `git diff --check` passed. No production change by this follow-on, no release intent frozen, no merge or live acceptance claim. Production branch was freshly observed at b92d3eb8d12920663a052744c9c0d2773a785e1a before this checkpoint.

The first integrated source a439ae4c45abc79f4fa78bd35e8289d97040a761 and candidate remain preserved on codex/courier-live-receipt-20261118 / PR #1977. Its official two-pass CI build and two independent candidate validators passed against the former J. The candidate is historical evidence only and must not be frozen for the new production base.

On 2026-11-17 at 22:10 UTC, PR #1974 advanced production to b009ee50a6b932368be8ad104c8c1b75e584b4e2. Its six changed paths (Android shipment read parity, tests, status/workflow and intent) do not overlap the ten task-owned paths. A prospective source merge retained correct intent bytes but failed the existing full-history source-only rule because the merge itself touched the intent ancestry. Therefore this successor source is reconstructed directly on the new J, with only the same ten task-owned paths; the earlier branch and artifact remain untouched. No history rule or gate is bypassed.

One ordinary authorized order-activity refresh subsequently succeeded. Its cached native history includes carrier dispatch and delivery observations, while payment transaction details were unavailable under the current provider permissions. This action writes only the existing activity/payment evidence ledgers; it does not establish canonical courier proof or a refreshed financial snapshot. Private order data is intentionally excluded from this record.

Required remaining evidence: full exact-source repository CI including real Mongo on the new base, immutable A/B build and intent, final-B gates, current shared lease ownership and actual publication verification.

## Remaining work / next safe action

1. Validate the reconstructed source on the current production base, including every required CI gate.
2. Investigate native acquisition using the preserved receipt after publication. Cached activity now has native carrier history, but canonical waybill-bound proof is still unconfirmed. Do not substitute a manual delivery assertion.
3. Obtain the actual scalar source-stage timings or authorized live query evidence before modifying order read performance. Existing owner-leading indexes and root projection already exist.
4. Preserve this WIP and tests on the dedicated branch. Keep private order data outside GitHub.
5. Respect the separate PR #1974 publication and lease; do not abort, reuse or publish over it.

