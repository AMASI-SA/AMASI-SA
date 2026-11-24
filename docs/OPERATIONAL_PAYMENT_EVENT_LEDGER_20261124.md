# Operational balances — event-driven receipts and COD confirmation

Status: IMPLEMENTATION BRIEF / NOT DEPLOYED
Owner approval: 2026-11-24
Tracking: #2107

## Isolation
Only operational balances. Do not create Qoyod invoices, alter Salla order states, or inadvertently double-write both legacy and new accounting stores. Identify the authoritative operational-balance ledger and deployment SHA first.

## Data and posting contract
- PaymentEvent: order_id, installment_id, provider, provider_transaction_id, amount_minor, currency, type (payment|refund|settlement), evidence_source, evidence_ref, occurred_at, observed_at.
- Collection/unique index: stable source transaction identity and event type plus partial-refund id; never deduplicate solely by order_id or amount.
- Lifecycle: observed -> awaiting_evidence -> confirmed -> posted. Rejected/invalid evidence does not affect posted balance. A late confirmation resumes same record.
- Posting is an atomic idempotent operation: persist evidence, event, ledger entry and state transition consistently, with unique enforcement and audit trail. Error/retry must be safe.
- For order initial awaiting-review: automatically post ONLY when actual successful payment proved. Additions become separate transactions under same order.
- Provider-specific reconciliation: use transaction IDs, currency, amount, refund IDs; for payout net amounts account separately for commission and VAT and settlement; prohibit fuzzy amount-only matches.
- Refunds: pending unless accountant explicitly confirms or trusted provider statement verifies; one financial effect regardless of source ordering.
- COD cash confirmed at delivered with verified collection to courier/shipping receivable; bank/POS collected at delivery becomes pending accountant validation until approved or independently verified by bank/acquirer statement; delivered state never rolled back because receipt is pending.
- Bank-transfer order progression is a reconciliation trigger, not independent proof of funds.
- Daily reconciliation and automatic retry + failure alerts; never silently resume long-missing historical data into opening balances.

## Required tests before enabling
1. Initial 300 Tabby, additional 150 Tamara: two receipts, distinct provider balances.
2. Additional unpaid: no posting; later provider statement confirms: exactly one entry.
3. Same webhook/csv resent >=10 times: no duplicates.
4. Partial/full refund manual vs statement in either order: one refund posting.
5. COD delivery before accountant POS/bank confirmation: status delivered, receipt pending, no bank balance change; approval posts once.
6. COD cash creates courier receivable; subsequent remittance decreases it and increases bank/cash once.
7. Revocation/correction is an auditable reversal, never delete history.
8. Rebuild since actual operational-balance start: opening balances preserved, difference report zero per account, no duplicate postings.
9. Multi-currency and fee/VAT precision; no fabricated payment provider when Salla order payment method changes.
10. Permission checks and upload parsing errors, replay after outage, concurrent-event race, backward compatibility and performance.

## Release safety gates
- Inspect production and Preview before code changes.
- Confirm source-of-truth ledger and schema by tracing UI -> API -> DB writes.
- Feature flag defaults OFF; no old-balance mutation on initial deploy.
- Backfill dry-run report first.
- Regression tests, code security review, preview UAT, bank/provider statement reconciliation, release guard.
- No merge or production deployment until explicit approval.


## 2026-11-24 phased implementation update
- A pure canonical Salla-capture adapter is implemented at `backend/operational_payment_capture_adapter.py`. It emits phase-ready event identities **only** from normalized successful captures; a paid observation or `awaiting_review` order status alone does not trigger posting. Different transaction IDs support added installments; native currency other than SAR requires an explicit FX path.
- The read-only phase-1/2/3 planner is at `backend/operational_payment_reconciliation_plan.py`: already-posted, conflicting, repeated and opening-overlap cases are excluded from candidate writes.
- The statement gate is at `backend/operational_statement_auto_confirmation.py`: automatic refund or extra payment confirmation requires exact independent native-event reference. Current `operational_provider_statement_intake._normalize_row` sets `event_reference=None` for Tabby and Tamara native exports, so upload-only rows from those parsers **must remain pending**, unless a separate verified transaction identity is established and bound. Never infer a unique payment/refund from amount + order alone.
- COD network evidence is different from bank-transfer evidence. Delivery completion must never imply collection.
- The current production refresh error `operational_source_setup_incomplete` cannot be attributed to a source until the allowlisted `cause` diagnostic is deployed and observed.

## Still required for functional live activation (NOT COMPLETED)
1. Verify production deploy SHA, actual environment flags and current data sources, and reconcile with the latest concurrent work.
2. Add an authenticated, idempotent ingestion service for original and added payment captures, sourcing from authoritative Salla transaction evidence (not `webhook_routes.py` Make-only flow).
3. Persist single canonical effects **inside existing operational balance CAS** without creating an independent parallel financial ledger. Protect at commit against reference/owner/order collisions and evidence changes.
4. Integrate statement confirmation with canonical source link and robust transaction fingerprint, not only row/order amount. Existing confirmation explicitly requires `confirmed_unrecorded`; must not silently bypass this without independent protection against baseline overlaps.
5. Implement pending-accountant POS/bank COD state in the operational workspace, with signed approval/audit, without blocking `delivered` status.
6. Add a recurring daily exception reconciliation process, bounded replay and freshness alert.
7. Historical dry-run per provider from the true operational starting point and opening balances; zero-difference reconciliation and Preview UAT.
8. Keep feature flags OFF until all gates pass; no Qoyod/general ledger changes or production deployment.


## Snapshot integrity preview (2026-11-24)
Implemented `GET /api/operational-balances/payment-events/integrity-preview` as an **owner-only, read-only** endpoint. It uses the existing `operational_balance_store.read` and checks recorded customer collections against existing canonical `capture:` facts with exact references; reports collisions, unmatched recorded collections, invalid snapshot effects, revision and data-as-of without modifying money. Results are explicitly scoped to the saved snapshot and NEVER authorize auto-posting, refund approvals or a historical backfill. Unit tests added.

Operational Payment Event Policy CI passed on commit `2b33ff6c26148f88c701facbee8b1b6d0df4a85b` (run 45447385570). No end-to-end Preview execution yet.

**Activation blockers still present:** authoritative proof ingestion from new Salla events; validated, owner-scoped transactional posting into the existing operational CAS (including concurrency and duplicate exclusion); provider-native event identity for Tabby/Tamara CSV refund/installment rows; daily scheduler; historical side-by-side reconciliation; checking production version and release guard. Keep the feature disabled and leave existing confirmed balances unchanged.


## Verified payment webhook integration / CAS evidence inbox
As of 2026-11-24, the verified `order.payment.updated` retry inbox now calls a **feature-flagged** hook after an accepted or stale canonical Salla order sync. New files:
- `backend/operational_payment_salla_event_capture.py`: extracts only normalized, time-stamped, provider-confirmed SAR captures, rejecting status-only/order-total-only observations.
- `backend/operational_payment_evidence_inbox.py`: records bounded source observations using the **existing operational balance Mongo CAS aggregate**; it treats an already-recognized `capture:<reference>` as already accounted, never as new money; two distinct payment references stay separate. It rejects conflicting order/provider/amount reuse.
- Existing `salla_integration/payment_update_inbox.py` retries evidence staging failures instead of marking the event complete. All proof observations are non-money; current balances/settlements remain unchanged.
- `operational_payment_snapshot_integrity.py` now reports pending-projection and already-recognized proof counts to the owner's read-only integrity preview.
- CI: Operational Payment Event Policy **PASS** on commit `eb11e70cd70d81b006c0e4feebcb0cb8ad602126` (workflow run 45449680733).

**Activation contract**: `OPERATIONAL_PAYMENT_EVIDENCE_CAPTURE_ENABLED` defaults to OFF. Do not set it to 1 in production yet. This flag enables *evidence staging only*, NOT automatic balance posting. A separate audited, tenant-bound balance-posting adapter must be written and reconciled before automatic receipts can be considered functional. Never count proof inbox entries as available bank/provider funds.

**Unresolved**: this is not a no-duplication cutover for old orders; no per-order targeted balance posting exists; no cross-provider statement event ID in existing Tabby/Tamara native exports; daily automatic exception review still lacks a live scheduler. Preview coverage, production SHA identification, and release guard remain outstanding.


## CAS source-evidence reconciliation (next WIP checkpoint)
The staged source evidence is now correlated with the provider/refund facts generated by the **existing** operational reconciliation engine. Implementation:
- `backend/operational_payment_cas_reconciliation.py` matches exact native reference, order, provider and amount to `engine.facts` during `operational_balance_service.refresh` under the existing CAS transaction.
- If the engine has already recognized the capture/refund: staged evidence status becomes `already_recognized`; no additional movement or obligation is created.
- If the canonical fact is absent, the event stays `evidence_received` and contributes **zero** to balances; if the identities disagree or a prior matched effect disappears, it becomes `conflict`.
- A `conflict` cannot auto-clear on a later refresh without separate reviewed resolution. Owner-only integrity preview reports separate pending, recognized, and conflicted counts.
- `payment_evidence_link` is available as a measured stage in refresh diagnostics.
- Unit tests verify no mutation of `engine.facts`, `openings`, or `movements`; incremental payment/refund identity matching and COD isolation.

**CRITICAL:** This is still *post-reconciliation evidence linkage*, not event-triggered financial posting. The operational engine continues to compute financial claims only when its authoritative refresh succeeds. The reported `operational_source_setup_incomplete` error remains an activation blocker, and the owner-enabled automation presently runs at a 7200-second interval. Do not claim instant automatic journal entry is operational, and do not reduce interval/bypass source validation merely to meet latency expectations.

The new stage is kept as a Draft PR and remains unexercised against live balances/Preview. Run the full test suite and UAT before considering feature activation. The evidence hook feature flag remains OFF in production.


## Owner-approved initial order awaiting review — 2026-11-24
The merchant approved immediate operational recognition for **new electronically-paid orders** arriving at status «انتظار المراجعة», but this is the *trigger*, not independent proof. The Salla signed order event must supply authoritative captured status/reference/amount/time.

Fix in the Draft branch:
- `salla_integration/webhook_order_sync.py` optionally enqueues verified `order.created` into the existing durable payment evidence inbox after the canonical source fence. The process is gated by `OPERATIONAL_PAYMENT_EVIDENCE_CAPTURE_ENABLED` (default OFF), and does not block order/fulfillment when OFF.
- `salla_integration/payment_update_inbox.py` now retains `order.created` alongside `order.payment.updated` with stable deduplication.
- `operational_payment_salla_event_capture.normalized_webhook_capture` accepts these two events, but only forwards complete native `normalize_payment` confirmed captures. Missing paid timestamp/reference/amount is not a financial event.
- Focused tests cover initial created event, incomplete payment evidence, existing updated payment event and retry behavior.
- Isolated Operational Payment Event Policy workflow run `45452409434` on commit `e7d6fecc671c04d29b4dda29ebed7bae3e956e60` passed.

This is **durable EVIDENCE delivery**, not a live bank/platform ledger posting. The current `operational_balance_service.refresh` remains the only owner of derived provider claims. Avoid duplicate per-payment writes into claims when refresh already derives the same capture from Salla. Production deployment SHA, Preview end-to-end tests and historical opening/provider statement reconciliation remain outstanding; do not merge/deploy yet.


## 2026-11-24 — Fail-closed accountant review for missing paid proof
- New `backend/operational_payment_evidence_review.py` retains insufficient payment evidence from authenticated Salla source events as a bounded, idempotent no-money review record in the existing operational CAS state.
- `operational_payment_salla_event_capture.py` now routes an `order.payment.updated` with missing reference/timestamp or an apparently paid `order.created` lacking canonical capture to review instead of silently dropping it. A newly created unpaid order without a payment-success signal is NOT added to financial review.
- If the source reports a paid transaction but provider is unknown, classify it as `unknown` for human review only; never select an arbitrary Tabby/Tamara/Salla account.
- The owner-only `/payment-events/integrity-preview` includes `payment_review_needs_evidence` rows with just a financially scoped order identifier and fixed error codes.
- These entries NEVER create `engine.facts`, obligations, customer collection cases, bank movements, or payment receipts. Replays do not reset manually closed review decisions.
- New tests include missing native proof, source retry, unknown provider, unfunded created order, owner separation, repeat event, and absence of financial mutations.
- Operational Payment Event Policy CI **PASS** on commit `02cc7eef78c351758504afc4d5559d7ff3b25a15` (run `45453801639`).

Remaining: the review is not yet an actionable accountant approval page; collection and refund flows still need integration to canonical identity, and the event-driven direct balance posting plus production refresh repair and Preview UAT are still incomplete. Keep payment evidence feature flag OFF in production and DO NOT DEPLOY.
