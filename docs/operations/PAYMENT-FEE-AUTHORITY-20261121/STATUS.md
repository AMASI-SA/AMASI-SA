# PAYMENT-FEE-AUTHORITY-20261121

Repository: AMASI-SA/AMASI-SA
Branch: codex/payment-method-setup-tariff-consistency-20261121
Reviewed base: 66aa13967156e8e279b5297caf697c155f720fd1
State: verified setup and dashboard monetary milestones; proof-based unpaid cohort integration remains in progress. Not a release candidate.

## Completed setup behavior

The dashboard notice and payment-method setup routes now read the same owner-scoped dated fee policies as operational balances. Canonical Emkan and card aliases reuse applicable tariffs instead of requesting duplicate legacy commissions. Active dates, SAR scope, rail, plan, conflicts and unavailable policy reads remain explicit. Valid plan-specific tariffs are configured setup; individual orders still require their applicable plan. The cache identity includes the policy contents and source mode. A commission save retains the existing settings CAS and returns the authoritative setup read.

Exact `waiting` and `wating` placeholders, including surrounding spaces and case variants, are excluded from fee discovery and commission creation. Source orders are not cancelled, marked paid or changed. COD, bank transfer and PayPal saved-fee policies retain their existing behavior.

## Validation

- Isolated Python environment, 110 backend tests passed: new fee-status tests, existing payment-method setup, authenticated setup routes and dashboard-card snapshots.
- Frontend notice, observed-method and fee-page tests: 47 passed.
- `git diff --check`: passed.
- Independent setup recheck: 82 tests passed. Setup checkpoint efe711d4ccb787448a05d709b04b73f3dd09e63e completed its applicable GitHub checks successfully; release-intent rehearsals were skipped because no new intent has been frozen.
- All fixtures are synthetic. No Production data, sync, financial write or order-status write was used for validation.

## Dashboard monetary milestone

The executive dashboard, V2 cards and dashboard ads collector now share per-order fee resolution with actual imported statement fees first, followed by the operational dated policies and permitted legacy settings. Policy/plan/date/rail/currency uncertainty remains explicit. Complete native payment timestamps reuse the existing payment normalizer; they do not fall back to the order creation date. Actual method identity is used consistently in electronic totals. Unsupported cancellations, split tenders and ambiguous refund execution stay incomplete instead of manufacturing a fee. Legacy settings-only aggregate rounding is preserved.

The UI keeps unknown fee/profit values unknown, with separate awaiting-collection and missing-fee messages. The existing tender coverage reader now treats exact waiting/wating aliases consistently; positive and partial payments retain their evidence.

- Monetary adapter, real dashboard-function execution, ads collector, V2 card and provider runtime/configured-fee regressions: 143 passed.
- Final independent monetary review: 95 targeted tests and 13 adversarial reproductions passed; no remaining blocker within this monetary scope.
- Frontend explicit fee completeness and existing fee views: 57 tests passed across 4 suites.
- Tender alias coverage: 31 tests passed, including isolated Mongo integration.
- These suites overlap earlier suites and are not added into a unique total.

## Remaining work and limits

Proof-based awaiting-payment financial cohort exclusion is being implemented separately. Its uncommitted helper/history work is intentionally excluded from this checkpoint. It must preserve source/reference order counts, existing payment and incurred-cost evidence, and all COD behavior. Do not claim that removing waiting from fee setup already removes all unpaid sales from reports. Legacy mezan_profit_engine and marketing_report_costs monetary consumers remain outside this dashboard milestone; historical analysis fees remain explicitly legacy.

Next: finish independent review, integrate and test bounded current-source and prior-history eligibility guards, checkpoint exact source and run CI. Then prepare a fresh protocol-v5 A/B release on the current Production base, rehearse, and perform separately verified publication. No release intent has been frozen for this task. Shared `/app`, Release Guard, Production branch and live runtime have not been changed by this task.

Read-only Emergent terminal preflight on 2026-11-21: shared guard active:false; shared HEAD 66aa13967156e8e279b5297caf697c155f720fd1; no tracked working changes. Live health reports source db141c0438edf04aa77ccd1122bc9c5008cf39d6, release rg5-500857bae382758ba238083f1f7f417b23a7e2dfb298aa3e2e99e7f4baf56b45, with verified identity, matching critical hashes and verified frontend all true. This is the prior deployment, not publication of this task. Recheck the guard immediately before any future shared workspace mutation.

## Preview delivery

Private design preview was separately updated and published successfully at source 7d4c05c234a231c5b8849447b4210428ebf69f7a, deployment appgdep_6b01cffe72048191bce2b7dbbc592604. It separates carrier freight/returns/COD/settlements, retains account context in movement forms and links existing statement functions. Preview simulations do not post financial entries. Browser visual recheck was blocked by the browser URL policy; code/VM checks passed. This preview is not a Production financial deployment.
