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

## Source candidate and browser verification

Source candidateA is frozen at0dd26a620a0acc4235d6fdfa0a3a8e714982fcae,
tree3b2205f13d26cf57014972ad940c715f62c541a9; source baseJ is
29ec1e07eb7d1a23647cadff5bac26149accfa99, resolved by the actual adapter helper.
No source edits are allowed without a new A and rebuilt identity.

Exact CI browser job132507776425 in run43614920574 passed all14 cases on
Chromium145 with sandbox enabled. Root downloaded artifact14121910716 and
verified ZIP SHA256da30bfa23a3b9b31d77d33ae1502f60fbb35bfd5e24ae3798283f4d68b8868b9,
exact A/tree, all20 source-manifest hashes and14 PASS records with no errors.
Root visually inspected summary desktop and sidebar mobile: clear Arabic RTL
layout, preserved unknown-data warnings, relevant tables and accessible navigation.
The two real downloaded workbooks were produced by the CI export scenarios.

Fresh original /app guard at2026-11-14T16:22:18.750951Z reports active:false,
HEAD29ec1e07. The other release no longer blocks rehearsal. Original user files
and Untitled-1 remain untouched; no /app mutation or publication for this task.

Mezan Release Readiness run43614920615 on exactA passed and produced its
candidate intent. Other exactA checks are still finishing. A clean no-hardlinks
independent clone was verified at exactA with all generated outputs absent;
governed locked dependency install is in progress. Next: local two-build proof,
freeze external candidate, compare exact CI artifact, then intent-only B when
all A checks and review gates pass. No lease is created during this preparation.

## Reviewed intent B

All11 exactA workflows passed. Web run43614920574 recorded4760 backend tests,
16 shipping/calculator tests plus18 subtests,774 frontend tests in52 suites,
14 browser cases and4 worker tests. Stock run43614920535 recorded4496 tests
plus7 subtests. Required collection/no-skip gates passed; totals overlap and
must not be summed as unique tests.

Independent clean-clone two-build47.777s, verification0.341s and external
freeze5.814s all completed with actual exit0. Both local and exactA CI candidates
passed load_candidate_release_intent and are byte-identical (1308035bytes).
CI artifact14122235456 ZIP SHA256:
cc035c56be8b664cccc7a4c7ed9b9d0886695f819197accc1c24fed10b412d57.
Intent byte SHA256:ad0a524b2de8da1a734e46bfeb4d851f9743ae28293eedb361075c41fcc4a4b2.
Frontend artifact tree:8b55f9ab1883c764826c27dd77b504fa55599bf0d84875f156565a9d8eaba5f4.
Runtime:rg5-5a1f26faa5108bc63621c816cdbd90697e422290a4245a460ff6020e6c97dc30.

Intent-only B:5fc08eef0b430b53911934d9f866ec7b13b61b47.
B tree:d636970f1a954530cf7845e408e05362af160cfa.
No source edits afterA; task branch nowB. Both exactB host20.20.2/24.19.0
clean-clone adapter rehearsals and all B checks remain required before normal
merge M. No lease, merge or publication has occurred for this task.

## Original-workspace runner templates

PR1921 templates are preserved under release-runners. Root inspected their
controlled diff and verified all manifest hashes. Independent read-only review
passed all four runners against immutable21d4a86f baseline: normalized ASTs
match except the intended additional lexical evidence-placeholder rejection.
Known A/J/B/tree/runtime/intent/actor pins agree throughout. Actual-exit records,
exact owned-lease checks, fresh nonignored-file preservation and separate
unsaved-buffer checks remain intact. Static compile and --help actual exits
were0; no live, /app or release action was run by the reviewers.

This approves templates only. M, fresh untracked count and successful new
rehearsal evidence remain invalid placeholders. Root must fill and review them
from observed evidence before original use. No template publishes or clears
another actor's lease. B CI is still in progress; no merge or publication yet.

## Normal merge M and filled rehearsal runners

All11 applicable exactB workflows passed. Readiness43615727641 host20.20.2
job132510889314 and host24.19.0 job132510889332 each actually ran and passed
adapter materialization, artifact/proof/identity/package-boundary verification,
and JSON build-metadata route checks; these were not skipped A jobs.

PR1921 normal mergeM:94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894.
Root verified through GitHub's commit object and local Git: exact parents
J29ec1e07/B5fc08eef, tree equals reviewed B d636970f, A is ancestor, A-to-M
changes exactly the intent file and raw intent SHA256 remainsad0a524b.
ExactM production-push CI is pending (Readiness43616365743,CodeQL43616365742,
Security43616365916). Do not publish before those required checks pass.

Original read-only preflight at2026-11-14T16:32:20.133559Z: guard inactive,
HEADJ29ec, correct production branch, clean tracked files,120 nonignored
untracked entries,0 ignored backend bytecode files. UI separately shows the
same1 unsaved Untitled-1 and120 pending changes; no editor save/discard.

The filled/ runner copies contain only the actual M and freshly observed
count120 substitutions. Independent static review passed exact byte diffs,
all4 hashes/lengths and unchanged function/entrypoint ASTs. Gate evidence
remains invalid until this release's successful original build. Templates
remain untouched. The original guard must be rechecked immediately before
original work; the rehearsal captures and compares a new full file manifest.

Emergent management tab6 stopped responding to browser focus; a fresh tab19
on the same original salla-analytics project opened successfully. Manage /
Overview shows prior100th publish1b4c4f6; no new task publication was clicked.
No task lease or original /app mutation has occurred.

## Exact-M gates and original build started

All3 exactM production-push workflows passed: Readiness43616365743,
Security43616365916,CodeQL43616365742. Root inspected terminal workflow
statuses and successful required jobs. Both actual M host adapter rehearsals
132512532313/132512532318 passed steps12–17; manual redeploy handoff
132513134494 also passed. The candidate runtime/source remained exactA/rg5-5a1f.

Reviewed filled runners were staged by the original code-server Terminal2
outside /app at /tmp/mezan-pr1921-runners-1j_g1nsn. Each downloaded immutable
8238e6ec file SHA256 matched the reviewed filled manifest before being written;
no stage executed during staging. Gate still has its invalid evidence placeholder.

Fresh original preflight at2026-11-14T16:40:27.543397Z observed inactive guard,
HEADJ29ec, correct branch, trackedclean,120 nonignored entries and0 ignored
backend bytecode. UI still had1unsavedUntitled-1 and120pendingchanges.
Root then launched the reviewed rehearsal once with --build through original
Terminal2. It reported evidence directory /tmp/mezan-pr1921-original-zu179mmf.
The command is running; no successful build is claimed yet. Do not rerun it or
send another command into Terminal2 until its actual outcome is known.
No prepare, prepublish or task publication has occurred.

## Original build actual success

Root observed successful original rehearsal outcome at2026-11-14T16:41:57.956406Z
and separately read its actual child record: command[yarn,build],cwd/app/frontend,
actual_exit0,problem:null,start16:40:50.910899Z,end16:41:57.777948Z,
elapsed66.867071s. ExactA/M/B/tree/runtime/intent matched. Fresh guard inactive,
HEADM94f6dfb8,trackedclean. Preservation evidence before_count120,after_count120,
changed_paths[],preserved:true. Separate UI still shows1unsavedUntitled-1.

The final filled gate changes only EVIDENCE to this successful private directory:
/tmp/mezan-pr1921-original-zu179mmf. Independent exact byte/AST review passed;
new gate SHA256e7240d4667bb5c69868140106ee717e03c6927fd35ae5d9a97d2f89766e9c33a,
11522bytes. Root will update only that staged /tmp runner, verify its old/new
hashes, then execute official prepare and prepublish at the ready publication UI.
No prepare/lease/publication has occurred yet.

## Prepared, prepublished, one publication started

Official prepare actualexit0,elapsed2.801461s,start16:44:31.597606Z,
end16:44:34.399060Z, exactownedactor codex-financial-section-pages-1921.
Official prepublish actualexit0,elapsed3.444751s,start16:45:23.628954Z,
end16:45:27.073703Z,ready_to_publish:true andfresh_owned_active:true,
exactA/M/runtime. Separate preservation afterprepare and afterprepublish
bothpreserved:true,count120; finalcheck16:45:27.344656Z.

Root clicked ManageOverview Republish, observed the explicit Free republishes /
Publishchanges dialog, then clicked Publishchanges exactly once at
2026-11-14T16:46:05.772Z. Overview changed to disabled Publishing... .
This is a running publication, not success. The priorpublishedversion remains
100th/1b4c4f6 until a new explicit completion appears. Do not click publish again.

Current original evidence:/tmp/mezan-pr1921-original-zu179mmf.
Current staged runners:/tmp/mezan-pr1921-runners-1j_g1nsn.
Current ownedlease:codex-financial-section-pages-1921, sourceA0dd26a62,
deploymentM94f6dfb8,release rg5-5a1f26fa... . Neverclear/reuse anotherlease.
Next: observe new explicit DeploymentSucceeded, run official verification once
with no wrapper timeout, preserve its actual exit, then final preservation and
fresh inactiveguard. Only then perform real financial-center UI timing checks.

## New publication completed; official verification running

Emergent displayed the new green Deploymentfinished Nov14,07:55PM in this
publication's b1458cee message, then the newly published versionecb9679.
This is newer than prepare/prepublish and the prior1b4c4f6. The UI's displayed
ordinal remained100th; do not invent101st. New log view showed current runtime
logs (not a complete Cloud Build transcript). No second publish was requested.

Root launched the reviewed official verifier exactly once in original Terminal2:
python -B /tmp/mezan-pr1921-runners-1j_g1nsn/pr1921_original_verify.py
 --evidence-dir /tmp/mezan-pr1921-original-zu179mmf
 --deployment-sha 94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894
 --actor codex-financial-section-pages-1921
It printed phase:started,prefix:/tmp/mezan-pr1921-original-zu179mmf/28-verify
and is still running. No wrapper timeout; do not interrupt or repeat it.
The full owned lease remains subject to its final official result and fresh
inactive status. Do not claim final verified success before its actual exit.

Original UI still separately shows1unsavedUntitled-1 and120pending changes.
Root's original Terminal2 is occupied; Terminal4 is an empty temporary shell
created for optional monitoring. Terminal1 is unrelated and must not be used.
Browser tabs changed outside this task: oldMezan10 no longerexists; do not use
unrelatednewtabs12/17. Root will create one fresh tab at the already verified
financial-center URL for read-only page checks while the official static-file
verification runs. Manualfinancialrefresh waits for officialidentity success.

## Live read-only pages observed during official verification

Fresh Mezan tab21 opened the original financial-position URL and showed the
new summary default and10-link Arabic RTL sidebar. Existing authentication
remained valid. Root checked actual report-content headings, expected table
IDs, aria-busy:false and absence of role-alert errors after each requested
navigation. Providers,couriers,drivers,employees andsummary all loaded.
All retained saved as_of14/11/2026,18:22Riyadh. Do not call this a new balance
calculation; no manualrefresh has been clicked in this release yet.

Observed click-to-confirmed-report durations (include browser-control overhead,
one navigation each, not pure backend timings): providers9.621s at17:00:24Z;
couriers14.912s at17:00:46Z;drivers9.774s at17:01:01Z;employees8.524s at17:01:24Z;
returnsummary10.663s at17:01:33Z. Initial fresh-tab summary was confirmed within
19.558s, an upper bound including a separate observation gap. Do not use that
as precise network latency or invent a percentage improvement.

Expected tables observed: providers+balance_origin;couriers+balance_origin+
courier_fee_review;drivers+balance_origin+driver_fee_review;employees+
employee_custody. Summary shows saved debt/profit/current/net displays and
separate pendingforecast; existing unknown-data warnings remain explicit.
Root visually inspected the actual summary/sidebar. No financial numbers or
customer records are included in this public handoff. Official verifier is
still running; original Terminal2 must not be interrupted or reused yet.
