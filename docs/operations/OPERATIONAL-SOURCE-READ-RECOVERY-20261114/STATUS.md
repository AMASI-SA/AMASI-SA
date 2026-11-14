# Operational source-read recovery — 2026-11-14

## Scope and authorization

The owner asked to continue after the published financial sidebar pages.
The remaining goal is a successful, bounded manual financial-source refresh.
Prior explicit repair, merge and publication authorization remains in force.
Preserve the user's demand-only policy: no automatic refresh, polling, detached
work, provider synchronization or real financial transaction as a test.

Repository: AMASI-SA/AMASI-SA.
Task branch: codex/financial-source-read-recovery-20261114.
Starting production base: 77e4acf33e8f5432d97bda5d7208983f0fadc599.
This includes the unrelated campaign operating-cost/executive-parity PR1924.
Prior financial pages PR1921 and all report tables must remain intact.

## Resumed evidence

The previous final documentation checkpoint is
1b192b0af21fd7efd48507a58544561f10292505 on
codex/financial-section-pages-handoff-20261114. Root fetched and verified it,
read AGENTS.md, the task status, PR1921 and the final available Issue1006 page.
Issue1006 is already full at2500 comments; do not repeatedly retry writes there.

PR1921 was published and officially verified. Its single post-release manual
refresh retained the dated saved report and displayed an orders source-read
timeout. The actual response status/detail was not captured. Source mapping
identifies unified_orders/operational_source_read_timeout, not a proved live
HTTP status or a proved database root cause. Saved as_of remains14Nov18:22Riyadh.

Current live financial tab still shows that warning and saved timestamp.
No new refresh has been triggered in this continuation. No source/DB/provider
mutation or publication has been performed in this continuation.

Fresh original /app read-only preflight at2026-11-14T17:44:51.295264Z:
HEAD77e4acf33e8f5432d97bda5d7208983f0fadc599, correct production branch,
tracked clean,10 nonignored untracked entries, release guard active:false.
The original editor still has one unsaved Untitled-1. A separate temporary
Terminal3 was created for this task; original terminals1/2 were left untouched.
The new count10 differs from the prior release's120; this continuation has not
changed those files. Re-capture a complete manifest before any future /app work.

## Current investigation

Two bounded read-only reviews inspect production-safe diagnostic access and
source-read cancellation/runtime behavior. Existing logs are sanitized but do
not correlate stages to one request; existing request-local stage timings are
returned only on outer408, not the source-specific409 error branch.

The original /app development database must not be assumed to be production.
Historical records explicitly show different preview and production datasets.
Do not infer the authenticated owner or database from matching report dates.
Do not print credentials, customer rows or financial amounts.

## Verified instrumentation milestone

Remote checkpoint before this milestone:
7898f3be92da5c34250db4a7aa0c5111d8a21237. Git CLI push lacked credentials;
the authorized GitHub connector persisted the exact same tree, and root
verified tree equality before aligning the local branch. No force push or
reset was used. Issue1006 remains full; this status and the task PR carry the
continuation record.

The outer visible-refresh error boundary now attaches the existing bounded
request-local stage timings to the exact canonical four-string-field source
HTTP409 detail, after enclosing stages unwind. It preserves status, headers,
cause and Arabic message. Unrelated errors and the existing408 contract remain
unchanged. The frontend records only allowlisted stage/duration/outcome fields
under operational_refresh_source_stage_timings. It preserves saved reports,
visibility/abort/offline guards and manual-only behavior. No diagnostic query,
timer, retry or background work was added. The existing85-second deadline is
exposed only inside the request context for the source-reader budget.

Changed instrumentation files: backend/operational_demand_refresh.py,
backend/tests/test_operational_balance_refresh_stage_timing.py,
frontend/src/pages/operational/visibleOperationalRead.js, and its test file.

Verification, all with real exit status:

- Immutable-baseline routed regression: exit1, intended missing stage_timings
  failure,1 failed in2.26s.
- Candidate stage timing suite: exit0,29 passed in2.20s.
- Five affected backend suites: exit0,255 passed in11.22s, before the separate
  reader-lifetime implementation. This is not combined verification.
- Both affected frontend suites: exit0,115 tests/2 suites,19.283s Jest time,
  governed Node22.23.2/Yarn1.22.22 and the existing dependency tree.
- Independent static instrumentation review: no concrete defect found;
  reviewed file hashes match. git diff --check: exit0.

Backend invocation uses PYTHONPATH=backend:backend/tests and the isolated
financial-sections-venv Python, with pytest --noconftest -q -p no:cacheprovider
--asyncio-mode=auto --tb=short, on refresh_stage_timing, source_diagnostics,
source_stage_timing, demand_refresh and demand_refresh_routes suites.
Frontend invocation uses scripts/frontend_release_toolchain.py exec --
yarn --cwd frontend node, the existing react-scripts/scripts/test.js,
--watch=false --runInBand --runTestsByPath, the visibleOperationalRead and
OperationalFinancialPosition suites, and explicit --modulePaths to the
existing dependency tree. Earlier cwd/NODE_PATH invocation errors were
environmental and were resolved without changing dependencies or test gates.

## Confirmed cancellation defect and current implementation

An actual Motor3.7.1 executor experiment shows that cancelling to_list's asyncio
Future can return while the current driver read remains active. The old code
fails both caller-cancel and deadline regressions on the production-pinned
PyMongo4.18.3. This is a proved ownership defect, not proof of the initial
production query's latency cause.

The separate reader implementation is undergoing integration review. It uses
read-local driver CSOT bounded by the existing source/request budgets, keeps
the active cursor advance owned through cancellation, and closes sequentially
after it settles. It preserves the full owner cohort,21-field order proof
projection,20001-row sentinel and owner checks. Generic profit component
readers require a separately stated coverage limit; no claim that every Mongo
operation is now joined is permitted.

The operational CI's two existing PyMongo test pins are aligned from4.18.2 to
the existing backend requirements pin4.18.3 so the regression runs against the
production driver version. No runtime dependency or lockfile was changed.

## Production diagnostic access and coordination

Fresh authenticated profile evidence was inspected without logging identity
values. The production database manager opened an expired SessionEnded view;
no production query, query-plan inspection or owner lookup was performed.
Automatic approval review rejected a broad database-page text dump because it
could disclose customer/financial data. A materially narrower metadata-only
check confirmed the expired session. No authentication/token workaround was
attempted. No credentials or customer rows were copied or committed.

The Emergent UI subsequently showed another publication in progress, outside
this task. Do not interfere with it or reuse its lease. Before this task's
release, fetch the current production branch, inspect the deployment state,
run a fresh original /app guard status, and preserve all unrelated files.

## Next safe action

Finish the owned-reader tests and independent review, then root must run fresh
combined verification on Motor3.7.1/PyMongo4.18.3. Persist the reviewed source
and open a Draft PR. Only after required CI and protocol-v5 sourceA/intentB/
mergeM/rehearsal gates may the authorized release proceed. After official
production verification, perform one intentional visible manual refresh and
capture its sanitized request-correlated outcome. If it fails, use that exact
stage evidence to choose the next repair. Do not infer a missing index or
truncate source coverage, and do not claim fresh balances until success and an
advanced saved timestamp are observed.

Production changed by this task: **no**.
Status: **verified diagnostic milestone; reader integration in progress;
no production recovery or refreshed balances claimed**.
