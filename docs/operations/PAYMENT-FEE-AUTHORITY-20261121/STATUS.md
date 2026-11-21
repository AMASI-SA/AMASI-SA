# PAYMENT-FEE-AUTHORITY-20261121

Repository: AMASI-SA/AMASI-SA
Branch: codex/payment-method-setup-tariff-consistency-20261121
Reviewed base: 66aa13967156e8e279b5297caf697c155f720fd1
State: verified setup milestone; monetary integration remains in progress. Not a release candidate.

## Completed setup behavior

The dashboard notice and payment-method setup routes now read the same owner-scoped dated fee policies as operational balances. Canonical Emkan and card aliases reuse applicable tariffs instead of requesting duplicate legacy commissions. Active dates, SAR scope, rail, plan, conflicts and unavailable policy reads remain explicit. Valid plan-specific tariffs are configured setup; individual orders still require their applicable plan. The cache identity includes the policy contents and source mode. A commission save retains the existing settings CAS and returns the authoritative setup read.

Exact `waiting` and `wating` placeholders, including surrounding spaces and case variants, are excluded from fee discovery and commission creation. Source orders are not cancelled, marked paid or changed. COD, bank transfer and PayPal saved-fee policies retain their existing behavior.

## Validation

- Isolated Python environment, 110 backend tests passed: new fee-status tests, existing payment-method setup, authenticated setup routes and dashboard-card snapshots.
- Frontend notice, observed-method and fee-page tests: 47 passed.
- `git diff --check`: passed.
- Independent review found and corrected false multi-plan setup alerts, a legacy save-response regression and malformed-policy availability handling. Final independent recheck is pending at this checkpoint.
- All fixtures are synthetic. No Production data, sync, financial write or order-status write was used for validation.

## Remaining work

The executive dashboard and ads cost collector still use legacy payment settings for monetary fee calculations at this checkpoint. A separate shared per-order fee adapter and integration are in progress. Do not mistake removal of the false setup alert for verification of all dashboard monetary totals. Current order-cohort handling of unpaid placeholders is also under read-only review; this milestone does not change sales eligibility.

Next: finish and independently test the monetary adapter, actual statement priority, dated policy/plan/date/currency gaps, legacy aggregate rounding and incomplete dependent totals; checkpoint exact source and run CI. Then prepare a fresh protocol-v5 A/B release on the current Production base, rehearse, and perform separately verified publication. No release intent has been frozen for this task. Shared `/app`, Release Guard, Production branch and live runtime have not been changed by this task.

## Preview delivery

Private design preview was separately updated and published successfully at source 7d4c05c234a231c5b8849447b4210428ebf69f7a, deployment appgdep_6b01cffe72048191bce2b7dbbc592604. It separates carrier freight/returns/COD/settlements, retains account context in movement forms and links existing statement functions. Preview simulations do not post financial entries. Browser visual recheck was blocked by the browser URL policy; code/VM checks passed. This preview is not a Production financial deployment.
