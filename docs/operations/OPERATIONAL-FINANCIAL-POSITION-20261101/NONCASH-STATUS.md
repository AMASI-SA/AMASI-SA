# Operational driver noncash review — WIP, not deployed

Repository: AMASI-SA/AMASI-SA. Branch: fix/operational-financial-followup-20261101, Draft PR #1677. Parent of this source slice: 9646a14f710f13aaae91b09e98c0db05d3c379b4, tree 4f964978948c7a7d000aaeb5f72deabf7b8172e9. Parent task #1675 is unchanged. The current PR head is the recoverable checkpoint; this document does not embed its own future commit ID.

## Scope and preserved decisions

Payroll begins on the cutover's Riyadh day itself; supplier approvals and actual deliveries after cutoff do not depend on customer-order age. Those implementations are inherited. No live payroll activation, opening rewrite or payment was performed.

This slice adds a dedicated operational approval path to the existing store-driver payment-review endpoint/page. It does not post native journals, replay sales/provider captures, change supplier cost, create a second delivery fee, or turn merchant bank/POS payment into driver cash custody.

- OperationalDriverReview uses one real snapshot/majority Mongo transaction with the existing owner revision fence and operational aggregate revision. A narrow capability wrapper rejects unscoped reads and undeclared writes. No standalone/mock transactional fallback, native journal capability or operational_atomic profile expansion is added.
- Approval pins source, review revision and actor scope; verifies a unique canonical order/actual driver/assignment, post-start delivery/collection, bound receipt bytes/hash, positive SAR amount and exact destination proof. Missing, contradictory, returned or already-consumed evidence fails closed.
- Bank transfer approval adds one incoming movement owned by the receiving bank and marks its proved imported statement row operational_consumed atomically. Original XLSX bytes/hash/row/reference/bank/amount/direction must match. Date-only bank rows on or before the cutover day remain blocked because their opening coverage is not known; this is not a prohibition on earned delivery fees.
- POS approval creates one confirmed receivable for the selected documented POS identity, not bank cash. Existing operational allocations subsequently settle it. Cash driver custody and shipping fee remain independent.
- Exact retry uses the same immutable decision and validates its retained monetary effect, including after later phase change. The owner coordination fence may still advance; money is not reposted. Rejection does not change money. Resubmitted review revisions do not inherit an earlier decision.
- Existing native review remains behind its original payload, pause, P02 and journal controls. UI loads explicit approval options, shows the financial phase, pins scope/revision, locks duplicate clicks, retains the exact request for ambiguous network/5xx retry, and discards stale list/options responses.
- Read-only report linkage verifies sealed facts against actual aggregate movements/obligations before showing receiving-account identity. It never adds collection display amounts to totals again. Missing fee snapshots stay explicit. Contradictory effects withhold net totals.
- The previous financial projection is copied byte-for-byte to operational_financial_position_core.py (56879afb81952ea1aa8e0362cfba6923926c0b0e); the normal imported wrapper composes the new read-only linkage. No runtime source patching or monkey-patching is used in application code.

## Local evidence

Exact ef2701f source archive was verified against 4,554 Git blobs; the evidence-only status document was overlaid at its verified 9646 blob. Local snapshot Git HEAD is not the remote source SHA. Production environment and database were not loaded.

Latest selected local regression: **418 passed**, no skips/failures/errors, including 53 new source/capability cases and 14 new report-effect cases. This uses mocks for database reads and does NOT establish transaction acceptance. Python 3.13.5; isolated dependencies; OS-installed bcrypt 4.2.0 copied only into that test target. No repository dependency or workflow was modified. AST/whitespace checks are required before committing.

The selected command covers test_store_delivery_noncash_review.py; test_operational_balance_driver_review_report.py; financial_position and routes; salary_context/cutover_day/start/consistency; engine; delivery_report_coverage; supplier_invoice_cutover; sources/source_fixes; and delivery_evidence. Evidence: local-final.xml and local-final.log in the task container.

Thirteen parameterized real-Mongo cases are written but NOT YET EXECUTED at this checkpoint: concurrent identical approval, full rollback after late failure, phase/scope/revision/authorization/cutoff failures, partial POS settlement, HTTP routing/retry and rejection. They are registered in the existing store-delivery evidence suite, whose existing CI already provisions localhost replica Mongo and XLSX dependencies. New frontend component tests are also pending execution; the existing operational frontend test glob includes them. No CI workflow or permission was changed to run these tests.

## Release restrictions / next action

Keep #1677 draft and unmerged. Run and read back exact-source candidate backend/frontend and Store Delivery CI. Fix any real failures before declaring the writer accepted; old payroll green checks are not evidence for this changed code. Preserve this checkpoint and append exact results when verified.

Still required: real transaction/native-regression and frontend acceptance; browser visual/print/export checks; full COD forecast and profit-period coverage; exact live v5 proof of the cost prerequisite and isolated integration with its security dependencies; production release gates. Existing report-wide completeness warnings remain conservative and do not claim all historic bank/POS records are linked.

Production changed: NO. Live approvals, balances, ledger, openings, /app, release intent, leases, parent branch and deployment: untouched. Issue #1006 is full; use this record and PR #1677 for continuity.
