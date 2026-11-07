# Financial table completion — bounded source candidate

## Owner scope
Complete the operational financial-position tables and remove incomplete labels only when supported by complete calculations/evidence, not cosmetically. Owner confirms current Tabby options are 3 and 4 installments with the same percentage. Do not reactivate the old BNPL sync or infer month labels as installment counts. Emkan refunds are paid by the merchant bank, not by Emkan. Existing Aramex/Emkan tariff approvals and financial movements must remain unchanged.

## This checkpoint
Base commit: fa3b8f2ff6b0084b5f88bbee45f8d8f88d726e71.
Original fee module blob: f65b7159c5b28d1b3bef23d7fcb33d7970739e7d.
Candidate fee module blob: d7af6674d9dce831eb41996d4343623660987aab.
Regression module blob: 0adebb711bd1d715c469f6db38c7022116accecf.

This is a narrow calculation improvement, NOT all-table completion or a deployment. An approved universal Tabby capture tariff can now be selected without a source plan when at least two overlapping distinct plan-specific V2 contracts agree with it in every non-approval field. No amount, rate, tax, effective date or plan is invented. All policy approvals are validated. Conflicting plans, monetary components, VAT basis/rounding, refund rules, duplicate identities, unknown fields and date-window uncertainty remain fail-closed. Known plans retain their original specific policy and result shape. Estimates remain estimates; the result includes the source policy identities.

An existing universal tariff is required: matching configured plans alone does not prove the complete set of plans offered by a merchant. There is no automatic policy write, scope inference or deactivation of longer plans. Production configuration was NOT inspected in this session. Therefore this patch alone is not evidence that the owner's current Tabby table will become complete. The live approved tariff scope and all components must be reconciled with the owner's stated 3/4-installment configuration through the normal authorized settings path, preserving historical effective dates. No additional numeric tariff is proposed here.

## Executed local validation
Only the pure fee module and new synthetic tests were materialized, not the whole repository. Original bytes were checked against the exact Git blob above before editing.

Command from the isolated work directory:

    PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 python -B -m pytest --noconftest -q -p no:cacheprovider backend/tests/test_operational_balance_tabby_uniform_fee.py --tb=short

Baseline: 16 failed / 23 passed, with the blocked missing-plan scenario and expected downstream guard diagnostics. Candidate: 39 passed, 0 failures/errors/skips. No real DB, API, account settings, source records or money movements were used. A separate deterministic 1000-case differential run (seed 20261107) over explicit plans, component VAT bases/treatments/rounding and retained/recalculated/full-only refunds matched the original module exactly and preserved all input values. These are local synthetic checks, not integration/production acceptance.

The new test name matches the existing Operational Balance Web workflow's test_operational_balance*.py collection; no workflow or gate was changed. Full repository integration, existing provider/engine/source tests, source-head CI and release checks still need to run. No independent reviewer or device verification is claimed.

## Still open outside this patch
- Table-specific source-coverage diagnostics and correctness gates (do not flip the common actual_complete flag globally).
- Actual Tabby fee-policy scope verification, native transaction/refund/settlement reconciliation and truthful estimated-versus-settled presentation.
- Emkan bank-refund path verification without double sale reduction or duplicate provider deduction.
- PR1781 current-base integration and actual Aramex expense/cashbox/no-duplicate acceptance.
- PR1794 audit-capture performance blocker; compatible mobile PR344 artifact/device acceptance.
- Financial-position profit/forecast calculations currently return unavailable values; same-period evidence and calculations must be completed, not defaulted to zero.
- Current authenticated operational snapshot, source warnings/unique affected orders and screen/export/print parity.

No main/Production branch update, release-intent change, merge, lease, publication, APK build, live approval, backfill or financial mutation was made. Keep this candidate Draft until integrated and reviewed; current owner authorization does not waive V5 or actual release acceptance.
