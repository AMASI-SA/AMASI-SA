# Inventory options, compact purchase draft and later receiving

Task: INVENTORY-OPTIONS-RECEIVING-20261122. Repository AMASI-SA/AMASI-SA.
Branch: codex/inventory-options-receiving-20261122.
Reviewed starting Production source: c302943e330bcdd534a4a5697b8ef813dfb7cb6e.

## Authorization and scope

Owner approved the native Mezan design preview and explicitly requested publication on 2026-11-22. Follow-up requires a visible later-receiving queue when goods have not arrived. Implement and verify before publishing. No live stock, supplier payment or customer data writes for testing are authorized.

Scope: canonical dependent product options and individual explicit deferral; stable option-ID stock identity; single top item editor and compact newest-first invoice draft; centered desktop form at 70% width; adjacent invoice metadata and selected checkmarks; operational purchase liability, separate actual receipt, and recorded payment/allocation. Existing stock entry remains independent of a purchase/payment. Preserve current valuation, writer controls and fulfillment eligibility; no stock reservation/consumption activation.

## WIP checkpoint — not release ready

- Options metadata and validation implemented with strict hidden-field, physical-dimension and deferral checks. Old request shapes preserved.
- New receipt source links immutable original invoice lines to physical placement in an owner transaction. Partial receipt, capacity, retry and a nonfinancial aggregate revision fence prevent duplicate/racing admission. Historical invoices remain unproven and are not treated as zero received.
- Existing payment allocation changes attribution only; residual account-payment calculation avoids a second supplier balance reduction. Cash evidence unchanged.
- Compact draft/editor and receiving UI implemented; parent integration verification in progress.
- No Release Intent frozen, merge, shared /app edit, lease, deployment or Production data change.

## Fresh evidence so far

- Options: 33 Python tests, 16 new frontend tests, 74 existing related frontend tests passed.
- Payment: 29 isolated real-Mongo tests passed, including native HTTP, replay, concurrency, ownership revocation and unchanged cash/supplier net balances. An earlier 75-test affected regression run passed before additional guard tests.
- Receipt: focused real-Mongo and transactional tests passed; broader legacy-response compatibility adjustments are being verified.
- Parent frontend: workflow, standalone-entry and workspace suites passed. Combined four-suite run: 66 passed / 19 failed. Failures are in existing purchase UI tests using the superseded multi-editor/save flow; those must be adapted and remaining legacy behavior verified, not ignored.
- git diff --check passed at checkpoint.

## Next safe action

Finish UI recovery and receiving tests, preserve legacy purchase customization behavior where applicable, adapt existing acceptance tests to the approved single-editor/review flow, and run integrated affected backend/frontend checks. Then open/recheck the Draft PR, produce a new exact source A / intent-only B using Release Guard v5, rehearse and publish only after the repository gates pass. Preserve unrelated source, remote changes and foreign leases.

Production changed by this task: **no**.
