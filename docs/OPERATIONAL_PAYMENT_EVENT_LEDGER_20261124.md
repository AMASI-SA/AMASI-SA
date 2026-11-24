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


## 2026-11-24 — First-payment direct canonical posting implementation (feature OFF)

A production-like read-only check of the merchant's current Salla orders exposed a material contract:
- Actual `بانتظار المراجعة` orders can be paid electronically by `tamara_installment` or `mada`, **and also exist for `bank` transfers**. The Salla status alone therefore cannot prove a bank receipt.
- Online order details may contain one `payment_methods[].transaction_reference`, exact `remaining_action.paid_amount`, and `has_remaining_amount=false`, but no `payment.paid_at`. A transfer order can also show a nonzero `paid_amount` even though its bank receipt remains unconfirmed.
- The original native-page payment timestamp must NOT be fabricated from the order creation date or webhook receipt.

Two strictly gated *canonical* provider claim writers have been added to the draft branch:

1. `operational_payment_direct_provider.apply_first_provider_capture` and `operational_payment_first_capture_writer.post_first_salla_capture`: one FULL original dated capture, exact reference, existing saved fee policy, no prior order claim/settlement, timestamp newer than operational cutoff and last successful source refresh; CAS commit, owner-scoped merchant recheck and audit.
2. `operational_payment_salla_order_page.native_paid_order_page`, `operational_payment_direct_order_page.apply_first_paid_order_page`, and `operational_payment_first_order_page_writer.post_first_paid_page`: when there is no original gateway `paid_at`, read the current owner-scoped Salla-direct order page; require native online provider, exact transaction reference and full cleared balance, source watermark match, genuine `بإنتظار المراجعة` status, and reuse `operational_order_page_payment.observed_balance` to create the **same canonical `provider_order_balance:<order_id>` fact / `provider:<order_id>` claim** already recognized by the normal reconciliation engine. The financial timestamp is explicitly a source-observation timestamp, not a gateway captured_at.
3. Both writers use the existing operational CAS, reject cutover/history collisions, existing claims, split payments, refunds and uncertain fee terms, never add bank money or a second accounting ledger. A normal source refresh can recognize their same immutable keys. Permanent history/fee conflicts go to the separate no-money review inbox. The source version and merchant ownership are reverified immediately before CAS commit.

**Flags remain OFF:**
- `OPERATIONAL_PAYMENT_EVIDENCE_CAPTURE_ENABLED=0`
- `OPERATIONAL_PAYMENT_DIRECT_POST_ENABLED=0`
- `OPERATIONAL_PAYMENT_ORDER_PAGE_POST_ENABLED=0`

The full isolated `Operational Payment Event Policy` CI PASS at `3039bd61d0690d5abe386e919faca9288e431d83` (workflow run `45461812392`). It covers pure first payment posting, replay, provider fee and cutoff guards, native paid page vs bank transfer, mocked operational CAS and owner/source change fencing. The order-page policy tests isolate the full production provider-history runtime because the tiny CI job does not install FastAPI; full historical/settlement integration still requires Preview UAT.

**Do NOT enable flags or release to Production yet.** Mandatory next gates:
- Integration/UAT on Preview with the actual Salla native source document/watermark schema and real fee-policy settings, including a new Tamara order and an online card/Mada order.
- Compare provider receivable totals before/after the direct write and after the subsequent full refresh; no additional gross money or duplicate fees; ensure all bank movement totals unchanged.
- Prove payout/opening overlaps, refund/additional installments and historical replay under production-like data.
- Resolve the current production `operational_source_setup_incomplete` condition and confirm current deployment/base SHA and release guard; ensure source refresh as_of is reliable (direct posting requires recent source within 3h).
- Confirm general-accounting activation gate remains isolated and does not cross-write GL/Qoyod.


## 2026-11-24 read-only production audit + direct-posting hardening
Authenticated browser read-only inspection (no refresh/write) at `https://mezansalla.com/operational-balances`:
- Last successful saved source snapshot: **2026-11-24 20:27 Asia/Riyadh**.
- Most recent full refresh: started **21:35**, finished **21:37**, outcome **FAILED**, `operational_refresh_timeout`.
- Balance page shows the last saved result; Salla/shipping/payment sources have failed/unknown source coverage, including provider native capture-date alerts. No conclusion that any individual paid order is uncollected is justified by these warnings.

**Safety defect discovered and fixed:** prior first-payment CAS writers accepted a snapshot whose `engine.as_of` was under 3h old even after a newer full refresh had timed out. A new owner-scoped `operational_payment_refresh_health_gate.py` requires the independent persisted refresh receipt to match the canonical `engine.as_of`, have known success, and have no newer failed/unknown/running attempt. Both dated-capture and native-paid-page writers recheck the receipt **within the candidate CAS and at the before-commit gate**. Absence/ambiguous metadata fails closed. This blocks the source-timeout state seen in production until a verified newer refresh succeeds.

**Duplicate reference defect fixed:** direct Salla order-page path must reject reuse of the same native `transaction_reference` by any other order's saved `provider_order_balance:` fact, canonical `capture:` fact, or confirmed manual provider customer collection. Such conflicts remain no-money accountant reviews. Added explicit cross-order collision tests and status-code allowlist.

**CI:** `Operational Payment Event Policy` success on code SHA `0493e0096b490cb267f083e14b57c9892a3f443d`, run `45463539027`. Unit tests validate latest failed/unknown refresh blocks money despite a saved recent as_of, still-successful source passage, owner mismatch, cutover and cross-order identity conflicts.

**Unresolved blocker:** Actual production `operational_refresh_timeout` is not fixed by the admission gate. Root cause must be diagnosed from bounded source-stage timings, primary-orders cursor acquisition and worker health, with a read-only Preview reproduction. No direct payment flags enabled, no merge/deploy, and no production financial write attempted.


## 2026-11-24 — Refresh timeout root-cause instrumentation (WIP / no deployment)
- Production read-only finding remains: last persisted successful full refresh 2026-11-24 20:27 Asia/Riyadh, most recent attempt 21:35–21:37 failed with `operational_refresh_timeout`. The exact slow stage is **NOT established** by that page; do not claim a fixed root cause.
- Existing `operational_demand_refresh` already generates bounded `stage_timings` and `primary_orders_reads` in its timeout exception. However `operational_balance_freshness.finish_attempt` stored only a generic, sanitized error code and discarded those timing observations, so the owner-facing `/freshness` read lacked the evidence needed for diagnosis.
- Added pure `operational_refresh_failure_trace.safe_failure_trace`: closed vocabulary of source/calculation stages; exact numeric duration and bounded cursor timing/counts only; no order IDs, amounts, exception text, URLs or customer information; malformed/untrusted diagnostic data is removed without impacting the actual failure.
- Updated `operational_balance_freshness.finish_attempt` to store the sanitized bounded trace in owner-scoped `operational_balance_refresh_health_v1.last_failure_trace` on a failed/unknown refresh and clear it on successful authoritative refresh.
- The existing owner-scoped `read_freshness` reader now fetches only this small health metadata and exposes it under `last_attempt.diagnostics` for a failed/unknown result; no new expensive source read or financial write is introduced. If the attempt is later superseded by a successful snapshot, the stale trace is withheld.
- Pure isolated timeout trace test suite and additional health persistence tests added. Focused `Operational Payment Event Policy` CI PASS on commit `f10028a8bc64f32c64757a8893c52329bc4b309b`, run `45466076119`. The expanded health persistence tests are authored but have not been verified by that isolated job (the job runs only pure unittest modules).
- This diagnostic change does **not** authorize extending refresh deadline, cancel-and-detach of Motor cursors, changing owner cohort/window, dropping old orders, skipping shipping/provider evidence, or weakening CAS/financial safeguards. Run Preview, reproduce one failure, inspect `last_attempt.diagnostics` and correct its measured source read or calculation bottleneck. The production timeout is not yet fixed; all direct-payment feature flags remain OFF, PR stays Draft.


## 2026-11-24 — Live successful refresh trace reveals headroom and Salla health split
Read-only authenticated browser read, no writes:
- `GET /api/operational-balances/automation` returned HTTP 200 and a **successful** run at 2026-11-24T18:47:03 UTC (21:47 Riyadh), finished around 18:48:25 UTC; zero consecutive failures; automation interval 7200s.
- Total aggregate timing 74.25s against an 85s request deadline, approximately **10.75s of slack** (not a proven root cause of an earlier failure). Financial source cohort read **19,057** `unified_orders`, first row ~11.3ms, cursor scan 9.23s with a 15s local read budget.
- Significant overlapping elapsed stage timings: `source_bundles` 17.87s, `snapshot_unpack` 12.03s, `source_orders` 11.35s, `reconciliation` 10.40s, `orders_cursor_read` 9.23s, and `profit_snapshot` 25.33s concurrently with source work. Never sum nested/overlapping stage times.
- `GET /api/operational-balances/freshness` returned the *same successful aggregate as_of*, revision 16941, but overall `freshness.state=failed` due to failed **Salla/payment-source freshness**. Successful aggregator calculation is not proof of full source health.

Additional changes:
1. Added lightweight, no-output-change `bundle_order_normalization` and `bundle_cost_authority` timing stages around the main steps of `operational_remaining_bundle_sources.load_remaining_bundles` (full cohort and all evidence preserved). This will distinguish large 17.9s bundle stage preparation vs cost-authority reads in a subsequent **Preview** run.
2. Extended the two first-payment operational CAS writers' health gate: alongside matching `last_success_at` to the canonical `as_of`, require that the independently verified owner Salla scheduled source health is `salla_source_current` with `state=current`. Use bounded `operational_source_freshness._safe('salla', ...)` to ensure source lookup errors/timeout fail closed. Read twice: during candidate CAS preparation and before commit.
3. The production-style state observed (successful aggregate, failed Salla freshness) is therefore NOT eligible for experimental direct financial posting, even though the aggregate itself completed. New pure tests check source failed/unknown vs current cases, and sanitized bundle timings.
4. `Operational Payment Event Policy` CI PASS on code SHA `d06edaabb2b65165838090b7f2a310e45be83c1d`, run `45467422164`. This is isolated/unit-only proof; not integrated Preview UAT.

**Still required:** identify why Salla scheduled reconciliation reports failed, separately from the operational aggregate timeout, and measure Preview bundle substages. Avoid suppressing alerts, extending timeouts, altering cost eligibility, skipping historical orders, or enabling payment flags until sources are current and direct-versus-refresh zero-difference checks pass.


## 2026-11-24 — Actual Salla daily coverage contract discovered
Read of current GitHub source `backend/salla_integration/sync.py::_run_existing_orders_refresh` showed the daily existing-only order refresh always returns:
- `coverage_complete: False`
- `coverage_reason: provider_modified_since_filter_unavailable`
- `discovery: recent_creations_and_verified_local_source_activity`
- `truncated`/errors/incomplete counts and an explicit 500-candidate cap

This is by design: Salla's documented list API does not offer a provider modified-since filter that proves all historical updates. `backend/salla_integration/scheduled_reconciliation.py::run_due` therefore sets `status=partial` whenever `coverage_complete is False`, even if no individual order failed, and `backend/operational_source_freshness.py::_salla` correctly reports this as NOT current.

**Important consequence:** Do NOT mark the Salla daily source as `current` or change `coverage_complete` to True merely to release payment posting. This is a coverage guarantee, not just a technical retry. Re-running the same bounded discovery does not establish complete historical coverage. The experimental payment writers' global Salla-current gate will continue blocking in this architecture until an independently safe **event-scoped proof policy** is defined and proven against opening/payout/history, or full-provider update coverage is genuinely available.

New owner-only read endpoint `GET /api/operational-balances/payment-events/salla-schedule-health` reads only saved scheduler metadata, never triggers a sync or returns customer/order content. `operational_salla_schedule_diagnostics.diagnose_salla_daily_cycle` distinguishes the fixed known historical-coverage limitation (`state=partial_coverage`, `reason=salla_modified_history_coverage_unprovable`) from a real failed attempt with errors, unknown sources, or incomplete metadata. Both remain `eligible_for_financial_posting=False`. Sanitized diagnostic evidence includes status, attempt counts, last success times, coverage counters, and allowlisted error class; no raw IDs, amounts, tokens, or exception text.

Isolated Operational Payment Event Policy CI PASS at SHA `e47cbc6097178a5e4ebac5350314358126748bc0`, workflow `45470826597`. All payment posting flags remain OFF; no merge/deploy or money mutation. **Next**: Preview/real owner scheduler metadata review, determine any independent technical failures beyond this structural coverage gap, implement exact event-level admission only with signed Salla capture and strong source/settlement overlap guards, prove zero-diff re-computation; do not relax the global freshness state.


## 2026-11-24 — Separately gated per-event Salla source admission (WIP, default OFF)

The existing Salla daily `coverage_complete=False` constant stems from the absence of a source-wide modified-since filter and cannot be replaced by a fake success. But a *new single paid order* has its own signed, tenant-scoped, native financial proof. Therefore, added a SECOND independent source-admission policy with strict limits:

- `backend/operational_salla_event_source_gate.py` uses flag `OPERATIONAL_PAYMENT_EVENT_SCOPED_SOURCE_GATE_ENABLED` (defaults OFF), requires `event_proof_bound=True`, current/last-due daily slot in Riyadh, scheduler status `partial`, exact recognized reason `provider_modified_since_filter_unavailable` and discovery `recent_creations_and_verified_local_source_activity`, `errors_count=0`, `incomplete_count=0`, `truncated=False`. It returns only `basis=new_verified_salla_event_only`, `historical_coverage_complete=False`, and `permission=first_payment_canonical_cas_only`.
- Missing/changed scheduler metadata, an old slot, failed/running/unknown status, any real Salla error, incomplete rows, truncated discovery, forged/unverified source or feature OFF all FAIL CLOSED. Full global Salla freshness remains failed/unknown and is never relabeled current.
- `operational_payment_refresh_health_gate.require_current_refresh_health` still first validates a saved successful aggregate as_of/owner receipt and rejects any later failed/unknown/running aggregate. If scheduled Salla health is not fully current, it invokes the event-only policy using a 1.5-second Mongo source metadata read under a 2-second bounded request, both at CAS preparation and immediately before commit.
- Native-capture writer verifies exact signed and durably staged payment proof *before* asking for event-scoped permission and rechecks it at CAS commit. Native-paid-page writer verifies owner, paid method, exact transaction reference, full closed paid balance and persisted source watermark; rechecks source financial fingerprint/merchant linkage before commit. Existing first-payment/old-opening/settlement/fee and cross-order reference guards remain in place. The event admission can never authorize COD, bank transfer, original historic capture, refunds, split payments, or an independent bank/Qoyod/GL movement.
- The event-scoped pathway is NOT production enabled and is NOT a claim that new-provider posting reconciles to the full refresh. It is built for Preview UAT on real first Tamara/Mada orders and subsequent no-double-count full refresh.
- **CI PASS** `Operational Payment Event Policy` on source commit `662bcbc87d19bb4222c968ea64545a500bc2ae66`, run `45471518369`; includes pure and isolated integration tests for no errors, correct daily slot, no proof, real technical failure, flag OFF, and merchant/financial source gates.

Activation prerequisites remain explicit: independent Preview source and opening/settlement reconciliation, full full-refresh zero-diff on provider claims and estimated fees, confirmation of financial expected-vs-confirmed value semantics, production SHA, active accounting gate/release guard, and owner release verification. Do not set flags to 1 in production.


## 2026-11-24 — Canonical booking-date parity correction (critical)

**Important financial safety correction to earlier WIP:** The existing `operational_balance_engine.reconcile` calls `operational_provider_dates.recognition_day` for every **new confirmed** provider receivable. That policy requires dated native captures covering the complete confirmed principal; a cumulative Salla order-page paid balance and its `updated_at` or `observed_at` cannot prove the original business date. The prior experimental `operational_payment_direct_order_page` wrote `provider_order_balance:<order_id>` and a confirmed provider claim based on this undated observation. A later canonical full refresh could reject it with `provider_original_capture_date_unproven`, or cause wrong-day provider fees. **This path must NOT post money.**

Correction implemented:
1. `operational_payment_direct_order_page.apply_first_paid_order_page` now rejects every eligible undated paid page with fixed `paid_page_original_capture_date_required` without modifying the state; no surrogate `order.created_at`, `updated_at`, webhook time or observation time is used as gateway capture time.
2. `operational_payment_first_order_page_writer.post_first_paid_page` now functions as read-only, owner-scoped **no-money review discovery** and returns `review_required`; the original feature flag remains for backward compatibility but cannot enable a financial page post. The existing payment-inbox workflow stages this as an accountant exception using fixed safe reason codes.
3. The actual first-payment **financial** writer remains the dated native capture `operational_payment_first_capture_writer.post_first_salla_capture`. Its signed native source normalized payment now retains `order_created_at`, and `operational_payment_direct_provider.apply_first_provider_capture` checks `operational_start < original_order_created_at <= original_gateway_captured_at <= now`. This matches the canonical reducer's original date admissibility and prevents pre-cutover or pre-creation capture posting.
4. Pure parity test `test_operational_payment_canonical_date_parity.py` invokes the same authoritative `recognition_day` rule and compares direct dated capture claim business_date, confirmed gross and provider report. It verifies missing native capture date is rejected and that neither source order creation nor later order-page observation can stand in for capture time.
5. Unit test fixtures for paid-page and CAS classification have been changed from expecting `posted` to requiring `review_required`, `movements`/`facts`/`obligations` unchanged. An undated page cannot perform a bank or provider write even if its flag is turned on.

This reduces automatic coverage for Salla online orders without original `paid_at`: they require an independently dated native transaction or accountant-reviewed evidence; do not claim that all under-review online orders are automatically posted. The source event-only daily coverage gate remains fail-closed by default.

**Current release state:** no merge/deploy, all flags OFF, no operational production balances changed. The new complete parity test suite is queued in GitHub CI at code SHA `9adb53853882ea26734195e6bf5a6fa87d8d3c8a` and must pass before treating this as validated.
