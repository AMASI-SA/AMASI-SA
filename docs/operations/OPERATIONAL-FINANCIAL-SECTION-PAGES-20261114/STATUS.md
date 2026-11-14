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
6. Opening/navigating issue GET-only section reads; currency changes use already
   read currency data and do not refresh sources. The explicit
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

## Independent backend review

The frozen seven-file backend slice passed independent review and a fresh148
test rerun (21.79s). Immutable29ec1e07 baseline wrapper/core modules were loaded
separately with import-identity assertions:20 old/full report outputs matched
after removing only additive view/revision/forecast_pending fields, and220 view
saved-money comparisons matched. Cases included multicurrency, wallets,
advertising, amounts above2^53 cents, valid/duplicate/awaiting driver approvals,
unknown sources, coverage gaps, uncaptured providers and inactive states.
Every prior table ID and balance-origin row remains reachable. No required
saved-projection error was swallowed; nine nonforecast views did zero cost
reads. Owner/query isolation and known-timeout/unknown-forecast behavior passed.

The narrow diagnostic amendment separately passed independent review and67
focused tests in12.86s. Backend runtime hash:
9d7d729760960168c226a331069c62a3c00c67644b4d66997010f836a5181878.
No stage diagnostics change source/commit/lease behavior. Remote diagnostic
checkpoint:5c44fa61abdfa455080e2ae79812739c99376a22.

Frontend review identified and is closing two concrete lifetime boundaries:
consume a manual refresh grant across auth-loading/scope effect restarts, and
fence an asynchronous full export through actual workbook serialization and
download, including auth transition and unmount. These are not yet claimed
verified at this checkpoint. No new source intent is frozen.

## Integrated candidate preparation

The synthetic fixture/harness now covers14 scenarios and generates36 actual
projections (12 views across saved/pending/applied states), with real confirmation
proofs and SAR/USD. Python fixture generation, the governed component build,
syntax checks and scoped whitespace checks passed. Local Chromium could not
start its sandbox: socket() failed with Operation not permitted before any
scenario. No flags were weakened or escalation attempted. Exact CI browser
execution, screenshots and downloads remain required; none are claimed locally.

The workflow now requires all14 scenarios, the existing proof images plus
summary/sidebar images and page/full workbooks, and the new spreadsheet
serialization tests. Existing task backend/diagnostic checkpoint5c44fa61 passed
Operational Balance Web (run43613751873), including real-Mongo backend and prior
browser cases; that is not evidence for the unfinished integrated frontend.

The first frontend run exposed a real old-ready-state/new-intent forecast race;
accepted attempt identity was added. The following focused run passed120 tests
in5 suites. A final hide/return-during-serialization race is being closed before
the next frozen rerun. Retaining the saved report also retains an already-open
pending-order dialog; synthetic cases now explicitly inspect, close and reopen
that dialog while preserving exact confirmation/refresh counts.

## Integrated WIP freeze

Frontend is frozen after125 tests in5 focused suites passed with no skips,
actual exit0. Scope: OperationalFinancialPosition, financialPositionViews,
financialPositionPresentation, visibleOperationalRead, financialPosition.css,
their component/API/presentation/lifetime tests, and the optional guarded
safeSpreadsheet download callback and its tests. Auth changes, accepted-attempt
ordering, consumed manual grants, and permanent export invalidation on hide or
offline through deferred serialization are covered. Existing callers without
the optional callback retain their behavior.

Focused command: CI=true python scripts/frontend_release_toolchain.py exec --
yarn --cwd frontend test --watchAll=false --runInBand --resetMocks=false
--runTestsByPath src/pages/operational/OperationalFinancialPosition.test.jsx
src/pages/operational/financialPositionPresentation.test.js
src/pages/operational/financialPositionApi.test.js
src/pages/operational/visibleOperationalRead.test.js src/lib/safeSpreadsheet.test.js.
Frontend whitespace check passed. Component SHA256:
a7274641dc993ef5052d96774ebeffa9494e01bd28eebf2706344068d72b705f.

The three synthetic harness files are also frozen; browser SHA256:
c6bbdfb968fc5e689fcd40ed2c806b96e3a67d8992f8a41f78b5780db2fd8d66.
Workflow YAML parses and the complete whitespace check passes. This checkpoint
is integrated WIP, not frozen release sourceA. Full frontend gate, final
independent frontend review and exact CI browser execution remain pending.

Next: run the full operational frontend gate, inspect exact integrated CI and
browser screenshots, fix any concrete failure, then freeze release sourceA.
Original /app has not changed; the latest observed guard remains another
actor's active lease and must be freshly inactive before release rehearsal.

## Handoff verification after integrated checkpoint

This documentation-only branch must not be merged into a frozen release lineage.
Source/task branch remains codex/financial-section-pages-20261114 at
0dd26a620a0acc4235d6fdfa0a3a8e714982fcae. Draft PR1921 is the review record.

Root ran the complete Operational Balance Frontend workflow selection under
governed Node22.23.2/Yarn1.22.22: actual exit0, 774 tests in52 suites passed,
no failed/skipped/todo tests, Jest87.098s (Yarn89.59s). The final independent
frontend rerun passed125/125 tests in5 suites,14.028s. All20 reviewed file hashes
(11 frontend,7 scoped backend,2 diagnostic files) remained unchanged afterward.
All identified attempt, auth, export-serialization and hide/offline races are
closed; no optional test expansion or source change is requested by review.

Exact integrated CI: Operational Balance Web run43614920574; browser acceptance
job132507776425 is in progress. Its frontend and canonical-worker jobs passed;
backend contract job is running. Local sandbox launch remains blocked before
scenarios; the CI screenshots/workbooks must be inspected before a browser PASS.

Current step: wait for and inspect exact integrated CI, then freeze A only if
all gates pass. Recheck original /app guard before any release rehearsal or
mutation. No new intent, lease, merge or publication exists. The existing global
refresh408 and actual live section latency remain unverified for this change.
