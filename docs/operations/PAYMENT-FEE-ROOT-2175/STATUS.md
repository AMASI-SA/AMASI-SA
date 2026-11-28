# Payment-fee root-cause repair — Issue #2175

## Milestone WIP-01 — read-only owner-scoped exception diagnostics

- Repository: `AMASI-SA/AMASI-SA`
- Task branch: `codex/payment-fee-diagnostics-2175-20261127`
- Baseline Production Git source: `19ea1404010b9c5e5135e5dbb4b40657157ff2b1`
- Scope: a bounded per-order fee-failure diagnostic in `dashboard_payment_fees`, propagated by `legacy_dashboard_reader`, with synthetic regression tests. No changes to formulas, policies, postings, refunds, ledger, order data or production runtime.
- The signed-in owner's already-selected dashboard cohort is the only diagnostic scope. Up to 40 issue rows are sent in the response, with exact total distinct order count and truncation. Only sanitised order number, payment method and reason code are sent. Raw transaction references and customer fields are excluded.
- Known partial fee amount remains separately available under the existing `payment_fee_authority.known_payment_fees_sar`; final net profit continues to be null when fee coverage is incomplete.
- Verification stage: **PENDING** GitHub CI after Draft PR. Not merged; not published; no Release Guard lease created.
- Next: verify source/test/CI, add the first-party dashboard display for exception order numbers, then implement bounded supported tender types and late-order-update date semantics against evidence. Do not infer actual production blocker before seeing the authenticated production diagnostic.
- Production changed by this milestone: **NO**. All other concurrent releases and guard leases are left untouched.

## Milestone WIP-02 — guarded BNPL + wallet read-only fee/cash coverage

- Prior WIP-01 parent: `778e46c3eab5b5912f630e8a5f129b84334383e0`; changes here are **new source and tests**, not a frozen v5 deployment intent.
- Accept one verified external provider from Salla card rails, Tabby, Tamara or Emkan alongside internal customer wallet. A second external provider remains unsupported without explicit allocation proof. Preserve exact order/owner identity, full original currency, refund, status, capture window, cross-order references and policy validity guards.
- Actual documented fees take priority; each fee-estimated external payment leg uses its **own** provider policy, and customer-wallet legs never create a new gateway fee.
- Subtract wallet principal from BNPL receivable, not from sales or profit; unknown wallet evidence leaves BNPL cash/net unknown even if an imported actual fee is valid.
- Synthetic regressions target Tabby, Tamara and Emkan splits; plan ambiguity and changing tariffs remain incomplete. The historical shipment-update timestamp issue and automatic reconciliation worker **remain pending**.
- Verification: **PENDING CI**. WIP Draft PR #2176, not merged/published; no Production change or financial writes.


## Milestone WIP-03 — historical timestamp provenance, no double-charge

- Removed the unsafe assumption that repeated Salla order-page paid_at fields independently date a capture. Their agreement cannot choose between two historical fee policies.
- Contradictory explicit external-tender timestamps now fail closed with provider_capture_timestamp_conflict.
- Regression tests preserve the original full interval in the absence of independent provider evidence.
- Correctness is more important than removing dashboard warnings by guessing.

## Milestone WIP-04 — bounded read-only native capture re-evaluation

- On a selected-owner dashboard query only, a late-updated split whose payment interval straddles a saved tariff change may read verified original Salla transaction rows from the existing order_payment_transactions_v2 reader.
- Use the existing canonical binding validator for owner, order ID, transaction ID, amount, currency, status, source version and reference collisions.
- A fee tariff can be dated per external tender only when **every external tender** has independently bound native captured_at proof; no date borrowing from shipping/order updated_at. Internal wallet is never charged a new payment-processor fee.
- Only up to 80 candidate orders and 800 source rows per dashboard calculation can be looked up; otherwise the pre-existing full-period ambiguity guard remains in force. Reads cannot create financial entries, settlement updates, scheduling, migration or backfill.
- Net cash KPIs are withheld if the relevant method's provider fee is incomplete. The known partial fee remains separately available for diagnosis, never as a final transfer or profit.
- Synthetic tests cover independent exact-match provider references, partial/unbound proof, a changed tariff, bounded loader calls, and incomplete cash KPI gating.
- Production acceptance remains **PENDING**: no completed CI proof for the latest head, no authenticated production cohort evidence, no financial total/net-profit comparison, no guarded release or live verification. This Draft PR must not be merged/published directly.

## Safety note

- This implementation is **read-time re-evaluation**, not a persisted historical backfill worker. An automatic persistent reconciliation worker with durable ledger-safe idempotency is still a separate prerequisite if required by business acceptance.
- No production data or accounting ledger mutations have been performed by this work.


## WIP-06 — historical observation cutoff and actual fee precedence

- Explicitly separate a historical dashboard's fee recognition cutoff from the more recent owner-scoped native-source observation timestamp. A late fulfillment event must not move an earlier captured payment into a different tariff period or turn a future capture into past sales.
- Clamp any date-only source watermark to the actual request instant, preventing future-dated source rows.
- Added regressions for historical loader behavior, post-report captures, actual accepted fee overriding an estimated tariff without double-counting, and foreign-owner transaction records.
- All work remains on Draft PR #2176, base `hotfix/prod-snap-meta-final`. CI checks on the latest HEAD were queued/pending during review; there is no verified PASS and no live financial reconciliation. Do not mark ready/merge/deploy until those gates are proved.
- Operational read-only source comparison does not write GL or financial state; no persistent idempotent backfill worker has been installed. Missing independent proof remains an explicit unknown, never a fabricated zero.

## WIP-08 — CI repair (2026-11-28 Riyadh)

- Workflow 46067163510 / backend-contract job 140735172112 on `98e13b4e3dad7fc77a1a6037b21a97341ae30da3`: 313 passed, 9 failed in the statement/historical suite; frontend, Security Gate, CodeQL and Release Readiness passed on that old head.
- Reuse the canonical strict Decimal helpers (`_cash`, `_positive`) in the wallet-split projection to resolve undefined names without relaxing money validation.
- Fix the request memo regression fixture to bind its selected order to the owner; add foreign/unbound negative coverage rather than weakening tenant filtering.
- Full-dashboard actual wallet fee test now uses a statement amount distinct from the tariff and verifies the public canonical rollup, fee VAT, net profit and receivable instead of expecting raw internal `fee_sources` metadata that the rollup does not expose. Existing lower-level tests still assert actual source identity.
- New CI results are pending. All figures in tests are synthetic, not merchant evidence. No production writes, lease changes, merge, publish or financial acceptance. Base comparison and merchant-period reconciliation remain mandatory.
