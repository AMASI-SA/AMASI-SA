# Inventory options, compact purchase draft and later receiving

## Unified publication supersedes the standalone release

On 2026-11-22 the owner explicitly authorized carrying the complete source
delta `9b4fb716b430c32073b5ea7abaeaff62646d9633..5d09108e6d33ed95b7b2946cdb5a382a49aefae7`
from PR #2065 into a new combined release with customer PR #2067.
Keep PR #2065 unmerged and do not publish its standalone intent
`9de88ea18b194d9d6b3e964642ae4e06e58a42e1`.
The current release owner is CUSTOMER-INVENTORY-UNIFIED-20261122, documented
in `../CUSTOMER-INVENTORY-UNIFIED-20261122/STATUS.md` and Issue #1006 comment
6238987123. The standalone instructions and results below are provenance,
not permission to publish independently or reuse their build artifacts.
The owner subsequently deferred publication until further operations join;
continue source integration/verification but do not publish this scope alone.

Task: INVENTORY-OPTIONS-RECEIVING-20261122. Repository AMASI-SA/AMASI-SA.
Current branch: codex/inventory-options-receiving-r2-20261122. Previous branch and Draft PR2063 remain preserved as superseded checkpoints.
Reviewed current Production base J2: 9b4fb716b430c32073b5ea7abaeaff62646d9633 (PR2062 external-carrier reprint release). Original J: c302943e330bcdd534a4a5697b8ef813dfb7cb6e.
Previous durable WIP:49524eb95a3498f2ab335ca49f2493610ed8bb2d; superseded source A1:1e89a97b351313692e7c6b6064e07bf3f364c612.
Previous r2 source A2:5ab27e875a0b3f675f9561fb4004289b70469349; the next source checkpoint includes the visual correction below and requires a fresh candidate intent.
The exact r2 source SHA and later CI/release state are recorded in Issue1006 bounded section INVENTORY-OPTIONS-RECEIVING-20261122.

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

## Concurrent production update and candidate review

A1 exact-head Security Gate, G47 and Release Readiness passed. Operational frontend and canonical worker passed; backend/stock were still running at this checkpoint. Browser geometry/RTL assertions passed, then the new harness timed out matching the supplier select by its implicit accessible name; corrected harness-only lookup now matches the exact visible field caption and its control, retaining explicit option-label lookup. Fresh build/syntax/diff checks passed; seven-scenario browser execution still required.

Production advanced through reviewed PR2062 to J2 during A1 verification. Its7changed files have zero overlap with the inventory task. The resulting newer-base shipping workflow invoked a test absent from A1; no shipping gate was skipped. New r2 source replays the inventory source delta onto J2 and preserves all7shipping files exactly. A read-only topology probe proved a normal merge would violate the v5 full-history intent-path check; r2 uses a fresh branch from J2, preserving the old branch/PR and all evidence. Do not reuse A1 candidate intent.

## Release state and next safe action

### A2 browser acceptance and visual correction

Exact A2 Operational Balance Web run45036362654 browser job137250885421 passed all7inventory scenarios. Artifact14759977607 was downloaded and its archive SHA2563a072a00f893b8c8ecf0377ad06680b7c01074a8702d3caac7811d43e3469e8e verified. Screenshots use actual React/CSS and synthetic fixtures only. Source manifest records57files. No receiving/payment call occurred when approving an unreceived credit invoice; later exact2of4 receipt kept2pending and unpaid balance415 after reload.

Visual review nevertheless found the global op-card max-width680px capped the inner invoice fieldset inside the centered70%panel, leaving unused left space; inherited op-tile column layout also made catalog results unnecessarily tall. Minimal scoped CSS removes the nested cap and restores horizontal native catalog rows. Browser assertions now check actual inner fieldset edges and row geometry in addition to the outer70%panel. This changes source identity; never reuse the A2 candidate intent. Exact new-source browser screenshots and gates remain required.

A2 Security Gate, G47, Stock Eligibility, Shipping Print Boundary and Release Readiness passed. Backend Operational Balance and Python CodeQL were still running at this source checkpoint. Intent-aware clean adapter rehearsals were skipped by the candidate-A workflow as designed; a source build is not rehearsal evidence.

No intent B frozen, merge, lease, deployment or Production write. Shared /app source untouched. Actual shared `python scripts/production_release_guard.py status` was observed returning `active:false` in a new terminal in the existing VSCode session on2026-11-22. The browser subsequently crashed again while reading Git status; that command's result was not observed. Recheck the actual shared guard and tracked source state before any shared source change; preserve unrelated untracked work. Browser connection may still require recovery.

Checkpoint corrected source; finish exact-source GitHub gates and review browser screenshots. Then review its fresh candidate intent from governed A/B build, freeze intent-only B without squash/rebase, pass v5 clean rehearsal, recheck Production J and actual shared guard, perform one authorized publication, require newer explicit Deployment Succeeded and original verify with3probes, then inactive guard. Preserve unrelated files/leases and never use reset --hard. Exact next state remains in Issue1006; do not republish from this source-status document alone.

Production changed by this task: **no**.
