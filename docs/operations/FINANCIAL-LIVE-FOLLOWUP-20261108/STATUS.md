# Financial live acceptance follow-up

Base: published PR1817 merge 6eed626bb8815c54db6d8cb46d45e97902e3db4f. Its source A8408b7df4b6ceb75e9a08565f3574023f99cc8a4 passed three official verification rounds (580 frontend HTTP checks each), runtime rg5-659dcf7fdb737c8ea67a88787f512de7a67fe25d283c59c7cc523040b1b43e73. Preview restored RUNNING; own lease closed. Do not rewrite that A/B pair.

## Observed failures and fixes

- Production UI attempted scope for the two approved short Tabby policies on 2026-11-01 and received provider_plan_scope_terms_differ. No scope saved; no tariff changed. Authenticated browser GET of the existing fee-policies endpoint proved all three component differences are `id` only; other differing top-level fields are plan_id, evidence, id and confirmed_at. Calculation fields, refund rules, VAT rounding and effective dates are identical. Compare arithmetic terms without component-local IDs, but retain the original immutable contract digest including IDs. Longer plans remain excluded unless explicitly approved; unknown order plans remain unknown.
- A real source-collector regression reproduces a prestart driver order with missing delivery time and no courier display name. Its delivery-phase marker was lost because display enrichment could not run, incorrectly blocking supplier/payroll/external tables. Preserve the exact resolver phase independently of display metadata. Coverage still allowlists exact known codes; unknown/owner/identity failures still fail closed. No guessed dates, new charge, backfill or opening modification.

## Verification

Both new behavioral tests failed on the published implementation and passed with the fixes. Full isolated Mongo 8.0.12 replica-set suite: 3526 passed, 15 warnings, 238.65s (runner run-finance-followup.py; no production database). An earlier three-file run had 121 passed and one environment failure because isolated Mongo had not been started; the full real-Mongo run resolves it without skipping the test. Additional two-variant 1000-amount arithmetic comparison (shared vs distinct component IDs) then passed: 15 focused non-HTTP tests. HTTP create/authorization/CAS/metadata-only tests passed in both ID variants during the full run. No frontend or courier API change in this follow-up.

## Open acceptance

Latest successfully rendered production financial snapshot 2026-11-08 19:47 Riyadh, report generated19:50. All groups remain incomplete. Tabby166 unique affected cases (159 fee-unmatched and7 undated), Salla25, Tamara9, unknown-provider12. Supplier/external coverage affected sources10199, employees10200; these are source diagnostics, not payable shipments or financial impact. Need next release plus one correct Tabby scope save, normal-worker recalculation, per-table evidence inspection and export/print parity. No claim all balances complete.

Current daily admin page read-only smoke: today3 entries, SAR975 incoming, Riyadh event timestamps, historical balance and recording time unavailable as explicitly labelled (not replaced by current balance). No real customer collection approved. PR344 remains unmerged; no courier APK required.

This checkpoint is development only. Fresh head CI, source A/build/intent-only B, clean-clone rehearsal, guard and publication still required. Shared /app changes require a fresh inactive guard; never close another conversation's lease.

## Lossless report transport

Synthetic profiling showed 12000 source diagnostics copied into table coverage132000 times, producing17,524,730 JSON bytes. Added opt-in `coverage_format=indexed`; the default expanded API is unchanged. The web client requests indexed coverage. Every full diagnostic is retained once in a catalog, with ordered per-table references preserving duplicates/counts/completeness and all rows, notes and amounts. Authentication and final scope recheck are unchanged. No courier endpoint changed. Exact reconstruction of the entire expanded report passed; indexed JSON6,677,755bytes (61.9%smaller), serialization0.0717→0.0203s, index construction0.3243s. This is synthetic transport evidence, not measured production latency.

13 authenticated financial-route tests passed, including complete reconstruction, unchanged source database, unauthorized401, invalid-format422 and unchanged default response. Relevant frontend28suites/375tests passed. Full final-source isolated replica-set suite is running again after these transport changes; not yet a final-head success claim. The full prior3526 result applies to the earlier two-fix source only.
