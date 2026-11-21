# PAYMENT-FEE-AUTHORITY-20261121

Repository: AMASI-SA/AMASI-SA
Branch: codex/payment-method-setup-tariff-consistency-20261121
Reviewed base: 66aa13967156e8e279b5297caf697c155f720fd1
State: verified setup, dashboard monetary and proof-based unpaid cohort milestones; independent frozen-contract extension completed, exact full CI execution pending. Not a release candidate.

## Completed setup behavior

The dashboard notice and payment-method setup routes now read the same owner-scoped dated fee policies as operational balances. Canonical Emkan and card aliases reuse applicable tariffs instead of requesting duplicate legacy commissions. Active dates, SAR scope, rail, plan, conflicts and unavailable policy reads remain explicit. Valid plan-specific tariffs are configured setup; individual orders still require their applicable plan. The cache identity includes the policy contents and source mode. A commission save retains the existing settings CAS and returns the authoritative setup read.

Exact `waiting` and `wating` placeholders, including surrounding spaces and case variants, are excluded from fee discovery and commission creation. Source orders are not cancelled, marked paid or changed. COD, bank transfer and PayPal saved-fee policies retain their existing behavior.

## Validation

- Isolated Python environment, 110 backend tests passed: new fee-status tests, existing payment-method setup, authenticated setup routes and dashboard-card snapshots.
- Frontend notice, observed-method and fee-page tests: 47 passed.
- `git diff --check`: passed.
- Independent setup recheck: 82 tests passed. Setup checkpoint efe711d4ccb787448a05d709b04b73f3dd09e63e completed its applicable GitHub checks successfully; release-intent rehearsals were skipped because no new intent has been frozen.
- Automated fixtures are synthetic. No Production sync, financial write or order-status write was used for validation. A later read-only Salla spot check confirmed the native waiting-payment payload shape; its anonymized shape is covered by a separate regression.

## Dashboard monetary milestone

The executive dashboard, V2 cards and dashboard ads collector now share per-order fee resolution with actual imported statement fees first, followed by the operational dated policies and permitted legacy settings. Policy/plan/date/rail/currency uncertainty remains explicit. Complete native payment timestamps reuse the existing payment normalizer; they do not fall back to the order creation date. Actual method identity is used consistently in electronic totals. Unsupported cancellations, split tenders and ambiguous refund execution stay incomplete instead of manufacturing a fee. Legacy settings-only aggregate rounding is preserved.

The UI keeps unknown fee/profit values unknown, with separate awaiting-collection and missing-fee messages. The existing tender coverage reader now treats exact waiting/wating aliases consistently; positive and partial payments retain their evidence.

- Monetary adapter, real dashboard-function execution, ads collector, V2 card and provider runtime/configured-fee regressions: 143 passed.
- Final independent monetary review: 95 targeted tests and 13 adversarial reproductions passed; no remaining blocker within this monetary scope.
- Frontend explicit fee completeness and existing fee views: 57 tests passed across 4 suites.
- Tender alias coverage: 31 tests passed, including isolated Mongo integration.
- These suites overlap earlier suites and are not added into a unique total.

## Awaiting-payment cohort milestone

Exact waiting/wating placeholders can now leave the financial cohort only when current native evidence is bound to the owner and order, explicitly records zero paid and the full amount outstanding, and a bounded historical check finds no previous payment or incurred-cost evidence. Missing, malformed, stale or contradictory evidence retains the row for review. Prior bank receipts, captures/refunds, COD collections, actual shipping/product/supplier costs and movement allocations prevent financial history from disappearing. Expected-only costs and stock reservations do not imply incurred cost. Large candidate sets use bounded batches with a shared deadline.

Dashboard server, V2 cards/chart and operational profit sources preserve original selected/reference order counts while calculating financial amounts from a separate guarded cohort. Unresolved review blocks final profit without replacing known fees or costs with zero. The UI distinguishes that review from missing commission setup and explains excluded unpaid counts. Reader cache identity changes and profit snapshot version v2 prevent earlier semantics from being presented as current; no historical snapshot or ledger is deleted. Normal authorized refresh rebuilds current snapshots. No source order status, COD policy, payment or financial entry is written by these readers.

- Helper/history/tender verification: 217 tests passed, including isolated real Mongo projection and history roundtrips. Additional native-shape, actual-fee and allocation protections were independently retested.
- Independent helper/history/cohort review: 197 tests passed; focused frontend behavior checks: 75 passed.
- Actual dashboard server and monetary regressions: 155 passed, including 12 Mongo-backed caller tests.
- V2/profit reader tests cover original counts, monetary exclusion, prior cost retention, exact waiting-to-paid transition, normal refresh and subsequent cached result. All six isolated real-Mongo caller tests passed; source-contract and corrected read-contract recheck passed 42 tests and five subtests.
- Final independent combined helper/history/server/V2/profit verification: 220 tests passed on isolated real Mongo. Counts overlap the focused suites above.
- Broad local frontend run completed 205 suites with no failed suite before the process was killed with exit 137. It is not a complete-suite pass; final remote CI is still required.

## Remaining work and limits

Follow-up validation on 2026-11-21: the owner-scoped Salla connector returned 39 waiting-payment orders for November 1–21. One full native payload had zero paid, full outstanding amount, a waiting payment-method row, and the payment_pending status. The current-source proof helper accepts that shape with the correct Mezan owner, identity and watermark. This is source evidence, not a Production Mezan calculation or a claim of complete reconciliation.

The monetary checkpoint's CI identified a legacy wrapper keyword compatibility regression (fixed), stale UI source-text assertions (replaced by behavior checks), and a frozen runtime response/read contract that is being extended independently for the approved fee and cohort semantics. The native bank-contract job passed on rerun without any bank code or test weakening. Release remains blocked until the final source passes the full required gates.

The independently reviewed frozen runtime oracle extension is included in this checkpoint. All untouched historical response values and source reads remain pinned, except the explicitly reviewed unknown-FX fixed-fee correction and new fee/cohort metadata. The two fee-policy reads are audited for exact owner, projection, operation and count. Focused gate validation passed 196 tests, and all 164 frozen reference records satisfy the independent expected-contract constraints. The available captured runtime response matches the fee extension byte-for-byte. Full execution of all 164 runtime responses remains pending native CI: local MongoDB 8.0.12 aborted before starting because the sandbox lacks its required CPU metadata. Its version guard was not changed. Legacy mezan_profit_engine and marketing_report_costs monetary consumers remain outside this dashboard fee-authority milestone; historical analysis fees remain explicitly legacy.

Next: finish the frozen runtime contract validation and final CI, then prepare a fresh protocol-v5 A/B release on the current Production base, rehearse, and perform separately verified publication. No release intent has been frozen for this task. Shared `/app`, Release Guard, Production branch and live runtime have not been changed by this task.

Read-only Emergent terminal preflight on 2026-11-21: shared guard active:false; shared HEAD 66aa13967156e8e279b5297caf697c155f720fd1; no tracked working changes. Live health reports source db141c0438edf04aa77ccd1122bc9c5008cf39d6, release rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45, with verified identity, matching critical hashes and verified frontend all true. This is the prior deployment, not publication of this task. Recheck the guard immediately before any future shared workspace mutation.

## Preview delivery

Private design preview was separately updated and published successfully at source 7d4c05c234a231c5b8849447b4210428ebf69f7a, deployment appgdep_6b01cffe72048191bce2b7dbbc592604. It separates carrier freight/returns/COD/settlements, retains account context in movement forms and links existing statement functions. Preview simulations do not post financial entries. Browser visual recheck was blocked by the browser URL policy; code/VM checks passed. This preview is not a Production financial deployment.
