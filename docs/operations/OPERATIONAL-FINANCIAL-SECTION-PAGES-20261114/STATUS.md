# Operational financial section pages — 2026-11-14

## Contract and current state

User requested faster financial-center loading by splitting payment platforms,
shipping companies, drivers and employees into sidebar pages, with the six
existing final summary tables as the first page. Prior authorization covers
repair, merge and publication. Root explained that ordinary navigation will
read the saved snapshot with its explicit date, while a named manual action
owns the existing shared refresh. Closed sections must do no work.

Base: 29ec1e07eb7d1a23647cadff5bac26149accfa99, latest observed production
branch head, preserving the unrelated campaign-hover profit/cost change.
Task branch: codex/financial-section-pages-20261114.

## Prior release evidence

PR1919 was published as a5eae68; its official three-probe verifier passed with
actual exit0 and582 frontend requests at15:18:09Z. Its lease closed normally.
All120 original nonignored entries and the unsaved Untitled-1 buffer were
preserved after verification. The original refresh nevertheless timed out in
two visible attempts and displayed saved as_of18:22 Riyadh. This remains an
open refresh defect; no fresh calculation success is claimed.

Latest original /app status at15:45:34Z is owned by a DIFFERENT active release:
actor codex-campaign-hover-profit-1920, deployment29ec1e07..., source2cf4d70d....
Do not modify /app, start a rehearsal, clear its lease or publish concurrently.
Root will coordinate by rechecking the official guard before release work.

## Implementation plan

1. Define whitelisted report views while retaining the existing full-report API
   default for compatibility. Return view/revision/start/as_of/session scope.
2. Introduce a dedicated read-only report-state path that validates the owner
   and packed financial/issue packets without materializing historical audit
   pages. Never change full store.read, mutation or audit validation paths,
   never fabricate an empty audit on a normal mutable aggregate.
3. Retain the complete balance arithmetic and global integrity/completeness
   checks, including delivery approval/effect conflicts, before scoping output.
   Balance-only views omit live open-cost projection and irrelevant order labels.
4. Default summary shows debts, current balances, profit rows and net profit
   from saved sources. Its two forecast displays load independently while the
   summary remains visible, bound to the same revision/as_of. Mismatches fail
   clearly; no cross-revision merge and no zero substitution.
5. Add sidebar pages retaining all existing tables and owner review controls:
   summary, providers, couriers, drivers, employees/custody, banks/cash,
   suppliers, advertising, wallets/other parties, and open orders/expectations.
   Unassigned details remain explicitly reachable.
6. Opening/navigating/currency changes issue GET-only section reads. One explicit
   manual button performs shared POSTrefresh; preserve cancellation, scope
   guards and the single bounded visible-attempt contract. No prefetch/timer.
7. Label current-page print/export clearly and preserve explicit full-report
   export. Never silently relabel a partial export as complete.

Do not add result caching in the initial slice unless a measured need warrants
its authorization/invalidation complexity. Do not patch speculative remaining
reconciliation candidates without evidence.

## Required evidence

- Full-report compatibility and independent per-view amount/completeness parity.
- Scoped backend reads prove omitted forecast/detail/archive work actually does
  not run; packet corruption/owner mismatch still fail closed.
- Global conflict and unknown-data semantics remain intact when details are hidden.
- Summary forecast revision binding, late response suppression and cancellation.
- No POSTrefresh on page mount/navigation; one explicit refresh; no closed-section
  work; existing confirmation scope/permission guards retained.
- Visible sidebar/mobile usability and all current table/control accessibility.
- Fresh integrated tests, reviewed source, official CI/release protocol, then
  actual live section loading measurements before claiming a speed improvement.

## Ownership and next safe step

Root owns integration, task documentation, GitHub mutations and deployment.
Implementation write surfaces will be assigned explicitly; agents must not
commit, change branch, install dependencies, touch live UI or write outside
their assigned files. No source changes yet at this checkpoint.

Next: finalize shared response/view contract and assign backend/frontend owners,
then deliver the saved-summary vertical slice with focused regression evidence.
