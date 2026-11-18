# Owner delivery policy clarification — 2026-11-18

## Current state: new source implementation under verification; no new publication

### Legacy financial-history compatibility follow-up

The first immutable source d4e20be4dc634e51cddd1528d698d33b93df15ef exposed the same three legacy driver cash/bank/card diagnostic failures in real-Mongo Operational Web run 44208875741 (5,302 passed, 3 failed) and Stock run 44208875899 (4,983 passed, 3 failed, 7 subtests passed). Existing financial facts and collections remained unchanged; an already recorded dated fee incorrectly changed its contradictory timestamp diagnostic to missing timestamp. The existing real-Mongo assertions are retained unchanged.

A seven-line resolver correction restores the established order-time diagnostic only when immutable financial history has already excluded status admission and the assignment has no delivery timestamp. It cannot convert a recorded dated fee into a status event. Three synthetic source-to-reducer regressions cover cash, bank transfer and card terminal, asserting the preserved shipping/COD facts, reports, obligations and movements. The new integrated local selection passed 465 tests in 8.62 seconds, exit 0, with the same 20 explicitly named real-Mongo cases deselected only because the local server is unavailable. Full exact-successor Mongo CI remains mandatory.

Independent read-only integrated review found no remaining source blocker after this correction. It independently verified the first source's exact base, 18 owned paths, 5,876 untouched base entries, eight unchanged earlier receipt/frontend blobs, and unchanged tracked intent throughout J..A. This follow-up requires a new immutable source and new official candidate: artifact 14385670385 from the first source is historical and MUST NOT be frozen. Source security, CodeQL and readiness jobs succeeded for the first source, but do not qualify its successor or replace the failed Mongo gates.

The latest observed PR #1969 checkpoint (00:37 UTC, November 18) records its accepted actual adapter, owned lease, successful prepublish, and one Publish action still being observed. That owner controls its verification and handoff. This task has not changed the shared deployment host, acquired a lease, published, or recalculated a live report. Exact predecessor publication and completed handoff remain required at the existing release boundaries.

The owner clarified the sufficient delivery rule: current authoritative Salla status is delivered and the order creation instant is strictly after operational started_at. Creation equal to the start retains the dated-evidence contract. This explicit instruction supersedes the earlier task assumption that every new delivery needs a waybill-bound transition date.

Repository: AMASI-SA/AMASI-SA. Successor task branch: codex/courier-status-policy-20261118; its Draft PR and this status are the canonical record because Issue #1006 is full. Preserved previous source: ef810aa10bc563c89f5c203a14b29f8b7452d6ab / PR #1983. This successor is reconstructed directly on new production base 8d7591f2908bd58aa006470236e693a1d40155fa, tree f261972640e341b22a2b99284a983e0c6598c2d5 (PR #1969). Its eleven ads code/test/status changes and exact inherited intent are preserved; no task-owned path overlaps them. The previous candidate/runtime is historical evidence only and MUST NOT be frozen for these changed sources. The inherited production intent remains untouched. A new immutable source, complete CI, new candidate and final intent-only commit are required.

### Implemented policy boundary

- Validate canonical owner/order/status authority and source watermark/version separately from optional delivery history.
- For qualifying new orders, retain a fully proved actual delivery date where available. Otherwise recognize the existing operational_delivery_status_v1 event with delivered_at null and immutable first recorded_at; do not manufacture a delivery time or canonical provider proof.
- Keep the current approved courier/driver, currency and shared-waybill protections outside optional-history fallback. Historical embedded shipment details cannot veto otherwise sufficient current Salla status.
- Keep existing dated facts, fee IDs, settlement allocations and posted amounts on their immutable contract. Existing status facts retain their original recording time even when optional historical dates arrive later.
- Normalize the effective customized Salla status consistently in collection and resolution.
- Keep COD collection, native driver earnings, tariff selection and completed/prepaid charges independent. An undated delivery can be confirmed while a fee remains unpriced or collection remains pending.
- Avoid provider history acquisition for delivery-only gaps that the new status rule already resolves; completion evidence remains separate. Acquisition also preserves recovery for immutable dated/legacy-fee paths using the existing indexed recorded-fee predicate. The existing state read additionally projects only obligations and movement allocations; packed snapshots hydrate obligations. There is no new audit-history query, per-order full-state scan, budget or cooldown change.

### Verification at this checkpoint

The new sufficiency regressions initially reproduced 16 intended failures; one additional new-test assertion incorrectly expected no existing fixed COD commission in the independently dated fixture and was corrected to preserve the existing 12.00 total. Source-authority tests additionally reproduced seven malformed-watermark cases (four silently accepted, three untyped AttributeErrors). The new focused suites passed 60 cases after the first implementation.

A broader affected run exposed intentionally obsolete optional-history expectations, which now assert status recognition without inventing a date. Independent read-only review also found two compatibility issues: pre-start out-of-scope validation ordering and acquisition versus immutable dated paths. The first was corrected with three explicit regressions; the second was corrected using the existing saved financial state and recorded-fee contract. Its regressions reproduced 14 failures and 10 passing controls before correction; all 24 now pass, including inline/packed states, paid legacy fees without facts, allocation-only fees, prior status facts and independent completion proof.

Current focused rule/authority/sufficiency command:
PYTHONPATH=backend:backend/tests /tmp/mezan-courier-test-venv/bin/python -m pytest -q --tb=short backend/tests/test_operational_balance_delivery_status_sufficiency.py backend/tests/test_operational_balance_delivery_status_authority.py backend/tests/test_operational_balance_delivery_start_rule.py -k 'not real_refresh_persists_unknown_delivery_date'
Result: 122 passed, 2 explicitly deselected real-Mongo persistence cases, exit 0. The local Mongo service remains unavailable; the exact-source CI Mongo gate is mandatory, with no waiver. An earlier broad local run was stopped after encountering the known unavailable server. A subsequent bounded affected run passed 330 tests and exposed two new assertion mistakes about the existing contract-driver fixed commission, now corrected. Final integrated local selection passed 462 tests in 5.85 seconds, exit 0, with 20 explicitly named existing real-Mongo cases deselected because the local server is unavailable. It covers all changed rule/authority/acquisition modules plus delivery history, waybill acquisition, manual confirmation, targeted match and failure receipts. The full exact-source CI Mongo gate remains required. No dependency/security scans are claimed for this new source yet.

### Live read-only observation and remaining work

A fresh signed-in UI read established that the selected target's creation is after the actual operational start and its status is delivered. The report still shows the previous saved snapshot and unchanged baseline counts. Customer/order details remain private and are not included here. No source refresh, provider proof write, financial mutation, new lease or publication occurred during this observation.

A fresh direct Git read discovered production advancement to 8d7591f2908bd58aa006470236e693a1d40155fa at PR #1969. Its current publication owner records source 23bacee592967ca3ea5264cbbe09d46410e4cf2c / B 0e024fb22c75dd38675b2d1af5a3fc6b2be14463 / intended runtime rg5-61f80b582f20bbcd74860f23ee92a7e68729a1da8f22516565736f1545995031. Its 00:27 UTC Nov18 checkpoint records an actual adapter pre-build failure on generated bytecode, with no lease or Publish; recovery remains that owner's responsibility. PR #1980 and #1982 now defer to PR #1969. This task must establish exact J publication and obtain a fresh completed handoff before release mutations. The user's merge/deploy authorization persists and does not require repetition.

Next safe action: complete independent integrated review, checkpoint this exact successor source and require every CI gate, derive and validate a new candidate, then follow the existing publication sequence. Before final intent freezing establish this exact J as published. Immediately before any merge pin live base and exact task head; an advancing base requires another reconstruction and candidate, never rewriting a frozen pair. After accepted deployment, perform the selected match and verify delivery recognition, recorded obligations and actual report changes. Do not claim a pending-count or pending-money decrease before that measurement.

Production changed by this new policy follow-on: NO. The previously accepted PR #1970 publication remains historical verified work.

---

## Preserved earlier continuation record (superseded policy assumptions)

# Courier live receipt recovery — 2026-11-18

## Current state: unchanged implementation reconstructed on the latest production source

Task: preserve the verified native result for one explicitly selected courier order when the subsequent financial aggregate fails. Retain enough sanitized timing evidence to diagnose the observed live source-read timeout.

Repository: AMASI-SA/AMASI-SA
Task branch: codex/courier-live-receipt-v3-20261118
Source preparation base: f2d806efc18964a2cc7c3a475346590cbe8b1262 (workspace-history optimization PR #1980).
The current base's immutable intent is inherited unchanged. PR #1974 has published its intended source and is completing its own official verification; PR #1980 is already merged and owns the next queued shared publication. Do not alter or reuse another owner's lease or prepared workspace. Do not freeze another intent until this exact base is independently established as published. Actual shared-workspace changes, adapter rehearsal, prepare and publication require the preceding owner's completed handoff and a fresh inactive guard.

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



## Superseding the verified second release candidate

The second source ac293e78568cc71d0d13007dd21f9deb281ad610 and intent-only B acec14403d45ff046ce30e3fb871c1af35b3d387 remain preserved on codex/courier-live-receipt-v2-20261118 / PR #1979. Its full exact-B CI passed:25successful checks,6expected scope/manual-publication skips, no pending/failing/neutral result. Actual CodeQL134428195580 reported no new alerts and0annotations. Operational backend5199tests, stock4880tests+7subtests, operational frontend1021tests, full security frontend4281tests, real Mongo and both actual host20/24 clean-clone adapter rehearsals passed. Two official tracked-intent validators and the prospective merge validator passed against the former J. These are preserved historical results and cannot authorize a different merged runtime.

On 2026-11-17 at22:55UTC, a direct live Git ref read showed that production had advanced to f2d806efc18964a2cc7c3a475346590cbe8b1262 through PR #1980 at22:46:33UTC. Connector PR/base metadata still named the older base; the actual remote Git ref and immutable commit objects revealed the divergence before any merge or shared-workspace modification. The new source changes operational_balance_workspace.py and its tests plus task evidence and intent. It does not overlap this task's ten paths. Its new runtime source must nevertheless be included, and the earlier frozen intent must not be reused or hand-edited.

This third source therefore inherits f2d and its tracked intent directly, carrying exactly the same nine reviewed code/test blobs plus this updated task status. No earlier source or intent commit is amended, rebased, squashed or force-pushed. Full current-source integration checks, a newly derived official candidate, independent validation, and exact final-B gates remain required.

The current live base observation remains the preceding Android source7f6bc85333b1189e40dd5f1138b841cf2241ee23/runtime rg5-c13e7f152e61e9ba1f799a1a78744bb998e3cdfb8be7cfdc09f97886868fa17e, newer platformversiondd9f327/Deploymentfinished18Nov01:42Riyadh. PR #1980's source a48657447dfb5bff48013ece455b745920403458/runtime rg5-5903cd2b08dd34d72afa632fce3c189767c18b4428bb9e403cc45d25b20543fa is merged but not yet independently observed published. It retains its queued release ownership.

No follow-on production publication, new target match, canonical proof save, pending-count reduction or pending-money reduction is claimed. Existing read APIs cannot reconstruct the exact historical report cutoff or value every pending order; compare actual stored snapshots and the selected order's recorded shipping/COD obligations separately. Never infer money from pending counts or treat recorded payables as pending-order value.

## Bounded Git-only queue and shared publication boundary

For this new successor, once exact base f2d is independently observed published and the new intent-only B passes all exact-B gates and review, an ordinary Git-only merge may be queued while PR #1980's owner completes its verifier. This changes no shared workspace or live deployment. Check live Git refs immediately before merge, require the unchanged reviewed base and expected task head, and verify the returned merge's parents and exact B tree. A moved base requires a new reviewed source/candidate; never resolve or rewrite a frozen pair. Every actual /app change, adapter rehearsal, own prepare/prepublish and publication remains reserved until the predecessor's complete three-probe acceptance/handoff and a fresh official inactive guard. This follows the existing protocol's separation of Git merge from deployment; no gate, lease or permission is relaxed.
