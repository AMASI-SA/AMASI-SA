# Inventory options, compact purchase draft and later receiving

Task: INVENTORY-OPTIONS-RECEIVING-20261122. Repository AMASI-SA/AMASI-SA.
Branch: codex/inventory-options-receiving-20261122. Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/2063 .
Reviewed starting Production source J: c302943e330bcdd534a4a5697b8ef813dfb7cb6e.
Previous durable WIP: 49524eb95a3498f2ab335ca49f2493610ed8bb2d.
The exact successor source SHA and later CI/release state are recorded in Issue1006 bounded section INVENTORY-OPTIONS-RECEIVING-20261122.

## Authorization and scope

Owner approved the native Mezan preview and explicitly requested publication on 2026-11-22. Follow-up requires later receiving for goods not yet delivered. Publication is authorized after verification; no live stock, supplier payment or customer data writes for testing.

Canonical dependent options and individual explicit deferral; stable option-ID identity; one top item editor and compact newest-first editable draft; centered desktop form at70% width; adjacent supplier/number/date and selection checkmarks. Invoice creates supplier liability; physical receipt and payment are independent. Existing stock entry remains separate from purchases/payments. Preserve current pending-valuation and writer controls; no fulfillment/reservation/consumption activation. A future order-matcher/valuation activation is outside this release.

## Implemented source candidate

- V2 option metadata supports verified conditions, fixed physical dimensions, per-option deferral, and labels separate from stable identity. Hidden values clear; optional blank is not literal no or automatic deferral. Hidden physical children are not required; unresolved physical conditions are rejected. Legacy payload hashes preserved.
- Compact purchase UI uses one editor, durable local draft, canonical duplicate detection, newest-first compact rows, edit in place, unsaved-change guard and review before approval. Same configuration rules in stock entry and purchases.
- Tracked invoices support not_received/partial/received/closed; historical untracked invoices remain unproven. Owner-only later queue at /inventory-v2?tab=receipts with original invoice lines, quantity/date/location and receipt history.
- Physical receipt uses original immutable invoice evidence, transaction, per-command replay, capacity/location checks and revision-only aggregate fence. No receipt money movement. Supplier returns reduce pending receipt without pretending goods were received.
- Existing supplier payment allocation changes attribution only. Supplier-account residual avoids a second payable deduction. Original cash snapshots/seals are retained through narrowly validated sealed allocation ancestry; missing/tampered chains never backfill historical balances.
- Durable workflow freezes request IDs before writes, validates restored phases and exact invoice/receipt binding, retries unknown outcomes unchanged, and allows later completion after a proven not-applied step. Separate operation keys prevent another tab from overwriting or deleting an uncertain payment/receipt. Payment readback retains receipt-enriched line fields.
- CI includes all new backend/frontend suites. New seven-scenario real React/CSS sandboxed Chromium harness covers desktop70%, metadata, selection, conditional fields, draft ordering/edit, mobile, unpaid/unreceived approval and later exact partial receipt. Synthetic loopback only; no live mutation.

## Verification at source checkpoint (overlapping, not additive)

- Focused frontend9suites:175 passed, exit0. Subsequent conditional-physical edge:44 Python and39 frontend passed, exit0.
- Payment allocation + original daily movement + new sealed-lineage integration:82 passed, exit0, disposable Mongo; independent tamper/binding review found no remaining concrete seal bypass.
- Receipt/opening/standalone earlier120 passed; option/metadata/payment/native-permission review110 passed. Receipt UI19 passed before final two-command display regression.
- Final two-command display regression reproduced the original failure, then one-line fix applied; fresh post-fix receipt/storage/configuration/projection44-case rerun passed, exit0.
- Integrated local backend305 cases:303 passed,2 failed during observed execution stalls with Mongo ServerSelectionTimeoutError in existing opening concurrency/rollback tests. Targeted unchanged-timeout rerun:both cases passed in1.73s, exit0; no gate bypass.
- Navigation/inventory frontend103-case rerun:102 passed,1 existing projection test exceeded5s during same stall; prior projection run passed. Navigation old-button expectation updated to approved review step. Fresh44-case targeted rerun including that projection and the new two-invoice recovery passed in3.11s, exit0; no timeout increases/skips.
- Browser harness builds successfully,57 source hashes. Local sandboxed Chromium launch is unavailable (socket Operation not permitted); zero browser scenarios claimed locally. Exact-source GitHub browser gate must pass and screenshots must be reviewed.
- Previous WIP CI: backend,stock,native lifecycle,release build passed; frontend gates failed two superseded purchase-button/editor expectations now corrected. These old-head results are not final source evidence.
- git diff --check passed. Dependencies unchanged.

## Release state and next safe action

No intent B frozen, merge, lease, deployment or Production write. Shared /app source untouched. Browser attempts to obtain actual shared guard status encountered crashed tabs and disconnected code-server; no active:false result was observed, so no release rehearsal or shared source change is authorized by the guard yet. Reconnect management UI, run actual shared production_release_guard.py status and continue only if inactive.

Checkpoint current source; finish exact-source GitHub gates and review browser screenshots. Then review candidate intent from governed A/B build, freeze intent-only B without squash/rebase, pass v5 clean rehearsal, recheck Production J and actual shared guard, perform one authorized publication, require newer explicit Deployment Succeeded and original verify with3probes, then inactive guard. Preserve unrelated files/leases and never use reset --hard. Exact next state remains in Issue1006; do not republish from this source-status document alone.

Production changed by this task: **no**.
