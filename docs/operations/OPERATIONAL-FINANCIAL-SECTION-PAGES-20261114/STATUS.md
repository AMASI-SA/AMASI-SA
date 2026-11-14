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

Latest original /app status at16:03:43Z is owned by a DIFFERENT active release:
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
6. Opening/navigating/currency changes issue GET-only section reads. The explicit
   manual button performs shared POSTrefresh. Existing explicit saved financial
   confirmations retain their one bounded, permission-checked POST+GET follow-up
   where needed to apply the confirmed effect. Hidden/unmounted attempts are
   consumed, never replayed on return. Preserve cancellation, scope guards and
   the single bounded visible-attempt contract. No prefetch/timer.
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
Implementation write surfaces are assigned explicitly; agents must not
commit, change branch, install dependencies, touch live UI or write outside
their assigned files.

## Backend WIP checkpoint

The scoped route, saved-state reader and report builders are implemented and
frozen for independent review. Full/default `all` still uses the original full
audit reader. Scoped reads validate owner and packed financial/issue packets,
omit historical audit page materialization, and retain global arithmetic and
driver approval/effect conflicts. Forecast and optionally full export are
bound to revision/as_of/started_at before and after their reads.

Changed backend files: operational_balance_routes.py,
operational_financial_position.py, operational_financial_position_core.py,
operational_financial_position_state.py, operational_financial_position_views.py;
new tests: test_operational_financial_position_state.py and
test_operational_financial_position_views.py.

Verified with Python3.12 in financial-sections-venv, PYTHONPATH=backend:backend/tests,
pytest --noconftest -p no:cacheprovider -q --tb=short against the two new suites and
existing financial_position, financial_position_routes, driver_review_report,
visible_report_reads and archive_read_lifetime suites: actual exit0, 148 tests
passed in19.14s. The initial summary regression first failed on the prior route
calling the live cost reader. Backend diff whitespace check passed.

Frontend implementation and independent review are still in progress. Root is
adding bounded request-local stage timings to the existing refresh408 response
for direct diagnosis when the platform log viewer is stale; this adds no reads,
timers or background work. Neither this diagnostic nor unfinished frontend work
is included in this backend-only WIP checkpoint.

Production changed by this task: no. Draft PR1921 is open; no new release intent,
lease or publish. Backend WIP remote checkpoint:
3cf27b03ebd1a79f9274a218b0bc93df85a8a87b.
The separate campaign release was publicly observed at15:52:38Z with verified
identity source2cf4d70d7e0c23c11048cad5c610e8c0c60d7eeb; its guard completion
remains unobserved. Issue1006 is full (2500 comments); durable task/PR records
replace further attempts to append there.

Next: independently review the frozen backend slice; finish and test frontend
navigation, forecast binding and guarded exports, then run integrated checks.
Recheck the official original /app guard before any release work.

## Timeout diagnostic checkpoint

Root added a fixed-vocabulary, request-local bounded tail of stage timings only
to the existing operational_refresh_timeout408 response. Each record contains
only stage, finite rounded duration_ms and a fixed outcome; no owner/order IDs,
exception details or additional DB reads. Existing deadline, source cancellation,
owned cleanup and admitted atomic-commit behavior are unchanged. Nested stages
overlap, so durations must not be summed as total runtime.

The extended deadline test first failed on the old response lacking timings.
Focused refresh/source timing, demand runtime/routes and archive-visible-batch
suites passed110 tests in12.72s (actual exit0). New cases cover malformed/private
values, bounded retention of cancelled stages, concurrent request isolation,
unchanged success and403 responses, and unchanged acquire/release calls.
Independent review is pending. The workflow now collects the73 new scoped
backend cases and requires their counts; it also requires all20 stage tests.

Next: finish frontend and synthetic browser regressions, independently review
the complete patch, and persist the integrated source before release rehearsal.
