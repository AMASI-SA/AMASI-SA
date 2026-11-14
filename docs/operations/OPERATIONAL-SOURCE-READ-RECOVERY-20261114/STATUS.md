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
The count10 here is grouped porcelain entries, not the complete file count.
A later full git ls-files inventory confirms120 files remain; this continuation
has not changed those files. Re-capture a complete manifest before /app work.

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

## Integrated source checkpoint

Instrumentation milestone is remotely recoverable at
ae539b0c8fb491f1474f239eb42e40816ccaa291,
tree a1cd7c892c28e8e7ea19b8f295a2c139c493c87c, parent7898f3be92da5c34250db4a7aa0c5111d8a21237.
Root aligned the exact fetched tree/index with compare-and-swap, preserving all
four reader working files. No source reset or force push occurred.

The reader correction is complete and independently reviewed. It covers every
primary refresh_source_rows call and ordinary rows only within the owned
load_collections scope, including forecast reads. Ordinary rows outside that
scope retain their prior to_list behavior and full payload. The source query,
all evidence containers, owner checks, row cap,15-second primary budget and
85-second visible budget remain unchanged. An exhausted budget fails before
entering CSOT. Sibling failure, caller cancellation and repeated cancellation
join the covered active Motor operation and sequential cursor cleanup before
releasing the reader scope. No automatic retry or replacement query is added.

Read-only independent source review checked actual Motor3.7.1/PyMongo4.18.3
implementation and production AcceptanceConfigDatabase/SnapshotReads adapter
compatibility. It found no blocking defect. Reviewed Git blobs:

- operational_read_runtime.py:a5947ab218536e49ba51344f8196734fcff6fed9
- operational_balance_sources.py:56b7a4edcb02fc44f12c5386bd4ac48c4a5de618
- test_operational_read_runtime.py:513ff884f43225e66ff8e9ee071a2cdb8b358df1
- test_operational_balance_order_source_projection.py:cca2dbfcaae4a5a850b8ef1a84e16320367bb4da

Focused final reader/projection tests passed89 cases in8.49s; adjacent forecast,
source, profit and supplier-preview lifetime suites passed182 in21.23s.
The real-driver wire fixture binds only a dynamic127.0.0.1 port, with synthetic
Mongo replies and finite cleanup. It proves stalled initial find and later
getMore operations finish, uncancelled, before error return; the responder sees
socket EOF. Initial find carries finite maxTimeMS. Three operations use
decreasing socket budgets with one common deadline and one initial query.
There is no real database, credential, environment loader or provider contact.

Root's fresh combined verification on the final integrated working files:

- Motor3.7.1/PyMongo4.18.3 using the isolated pinned overlay: actualexit0,
  **541 passed in30.10s**, no skips/failures. Two pre-existing Pydantic
  root_validator deprecation warnings came from recurring_obligations_routes.
- Both affected frontend suites using governed Node22.23.2/Yarn1.22.22:
  actualexit0, **115 passed/2 suites in15.45s** Jest time.
- All four reader/source/test blobs match the independent review above;
  git diff --check passed.

Combined backend invocation: PYTHONPATH includes the4.18.3 overlay first, then
backend:backend/tests; PYTHON_DOTENV_DISABLED=1; isolated Python -B -m pytest
--noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=short, against
test_operational_read_runtime, order_source_projection, refresh_stage_timing,
source_diagnostics, source_stage_timing, demand_refresh, demand_refresh_routes,
open_cost_exposure, balance_sources, balance_profit_sources,
supplier_preview_lifetime, source_conversion_lifetime and visible_evidence.
The frontend command is the exact governed two-suite invocation recorded above.

Scope limit: generic operational_profit_sources component callbacks still use
direct to_list. They inherit CSOT but are not routed through the owned cursor
helper; early cancellation there may outlive its waiter until that driver
budget expires. No claim of application-wide Motor ownership is made. Driver
CSOT also does not hard-preempt BSON decoding/thread scheduling, and failed
close may queue driver maintenance. The demonstrated guarantee concerns the
covered active source-read operation. Initial production latency remains
unmeasured, and a production recovery claim is not justified yet.

Latest fresh GitHub production branch is still
77e4acf33e8f5432d97bda5d7208983f0fadc599. No /app changes, release lease,
merge or publication have been performed by this task.

## Next safe action

Persist this integrated source and open its Draft PR; record the exact source
SHA and PR URL in the PR continuation record. Inspect all required exact-source
CI gates. Recheck the other publication and original guard before any /app
work. After protocol-v5 sourceA/intentB/normal mergeM, clean-clone rehearsal and
official three-probe production verification, perform one intentional visible
manual refresh and retain its sanitized request-correlated outcome. If it
fails, use that exact stage evidence to choose the next repair. Do not infer a
missing index, reduce coverage or claim fresh balances until the saved date
advances after a successful refresh. Subsequent release handoff documentation
must not modify frozen A/B governed source; use the task PR and a separate
documentation checkpoint branch as needed.

Production changed by this task: **no**.
Status: **integrated source locally verified; exact CI/release/live recovery
pending**.

## Reviewed source A and intent B

PR: https://github.com/AMASI-SA/AMASI-SA/pull/1925 (Draft at this checkpoint).
Frozen source A:616db2a06344027020fafc00c938f71c1e2b1874.
Source tree:e9292edd6f82592e3480d9f06336d080fcc79ee7.
Reviewed base J:77e4acf33e8f5432d97bda5d7208983f0fadc599.
Intent-only B:bafaebb4a002aea275854b7862d192c8eeb7b441.
B tree:b992134c7ec89bd9d56fa7eca8ccc214cdf51d5a.
Intent blob:282d38874c40e5f8e933bc4f4c20bbf5b351c254.
Intent byte SHA256:b41dff04a3c8227ab788a51469fe8254e32123d1a7057643569ff1b06e0284dd.
Runtime:rg5-ff873fd1a7abfb3822d1febfaccb4a4c8a063baaca1ed4b23218f245f71d3c87.
Frontend artifact tree:d6bd189f78c5bb7467fa0b0c7a8edc2e0c36f8d22f6b5ab23242b0a9a0dc25bd.

All exact-A GitHub check-runs settled:27 total,21 successes,6 conditional skips,
zero pending/failure. Root independently re-fetched and checked every head SHA.
The skipped jobs are the candidate-A clean-clone/manual-redeploy jobs and four
unrelated scoped Snapchat/publication jobs, not skipped tests. The reviewed-B
clean-clone matrix must execute before merge.

Operational run43624962345, exact sourceA:

- Backend4799 passed,8 warnings in222.84s; separate shipping16 tests and18
  subtests passed. The full operational JUnit no-skips/no-failures gate passed.
  This job installed production-pinned Motor3.7.1/PyMongo4.18.3.
- Frontend783 tests/52 suites passed in19.638s.
- Browser/mobile/visibility acceptance: all14 scenarios passed, including
  scoped sidebar GETs, saved-value retention on explicit source failure,
  hidden-page cancellation and separately guarded exports.
- Canonical background-worker exclusion gate passed.

Release Readiness run43624962313 passed sourceA preflight, both host contracts
and governed two-build/candidate freeze. The candidate artifact14125354026
ZIP digest b1018243b59cc1138443e4bfa067991d83dad7298af256f7d06ab2336ea766ef
was verified before extraction. It contains only release-intent-v5.json.
Other independent gates passed, including CodeQL, security, employee, tracking,
G47 and stock. Stock's4513 tests/7 subtests use its separate4.18.1 dependency;
they are compatibility evidence, not the4.18.3 driver-boundary proof.

Root's local governed A/B build completed with actualexit0 at18:17:06.372749Z,
elapsed49.663188s. It used Node22.23.2/Yarn1.22.22, the exact reviewed public
origin, and the documented MEZAN_RELEASE_BOOTSTRAP_INTENT=1 candidate-A mode.
The first local invocation omitted that candidate flag and correctly rejected
new source against the old tracked intent; no source or guard was weakened.
After the official candidate-source checks, the corrected invocation completed
both clean builds. Retained proof SHA256:
e830373d93cf42a27d034ca8ff75c07ae049463fa6825e7a4d65ac4c3731e0cf.
Official frontend verification and candidate freeze/load validation exited0.
The local1,308,037-byte intent matches CI bytes exactly. The source-A loader
reported the expected absence of ignored backend/release_identity.json; actual
identity materialization remains part of the forthcoming adapter rehearsal.

B was created through the GitHub connector as A's direct child changing only
release/release-intent-v5.json. Local fetched tree/intent-byte verification and
compare-and-swap alignment preserve the reviewed A/B contract. No amend,
squash, rebase or force push is allowed after this freeze.

## Original environment and prepared helpers

The previously running other publication displayed Deploymentfinished Nov14,
21:01Riyadh. The queued original guard status returned active:false; it was not
interrupted. A fresh subsequent check at2026-11-14T18:15:29.387202Z confirmed
HEAD77e4acf33e8f5432d97bda5d7208983f0fadc599, tracked clean,120 full nonignored
untracked files and active:false. The earlier10 is directory-grouped status,
not evidence of file removal. Original terminals1/2 and unsaved Untitled-1
remain untouched. This task only used its separate Terminal3 for read-only
status and inventory checks.

Four adapted release helper templates live in release-runners on the separate
financial-source-read-handoff branch recorded below.
Their sourceA/baseJ/actor are fixed for PR1925; B/M/runtime/intent digest/fresh
count/evidence parameters remain unfilled until the final configured review.
They retain fresh complete manifest capture, preservation of mode/bytes/paths,
inactive guard checks, exact A/B/M ancestry and intent-only diff checks,
governed adapter build, exact identity materialization and actual child exits.
The official verification wrapper has no timeout and now ignores Ctrl-C while
joining its verifier; it never interrupts or detaches that child. Other guard
status probes retain existing bounded behavior.

Templates passed AST/compile/whitespace checks without execution or import,
and independent static comparison found no concrete defect. SHA256:

- rehearsal:21ea2d6d4871656964034e2788118ccb4728953a7960db1d5d38c9ee6dd5b36e
- gate:0c3935a36448c32370de93618592804c78bb2123215084b35b7f81481a511039
- verify:c6e65308005f43109f19cf86d49dee7db3f171696280de10c85ff74d04bfddd5
- preserve:fa97282f13f57ea758fb628ba0e93c681e35e94f5a6473dd7cf294ce9f10e695

These templates are not evidence of rehearsal, a lease or publication. They
must be filled consistently, independently rechecked, and fetched by exact
reviewed commit/bytes before any original-environment execution.

The separate documentation branch is
codex/financial-source-read-handoff-20261114, starting at sourceA. It is not a
release branch and must not be merged into frozen A/B. The task PR carries its
exact checkpoint SHA once persisted. Issue1006 remains full.

Next: inspect every exact-B gate, including both clean-clone adapter jobs;
fresh-check baseJ, then normal merge preserving B's tree. Fill/review/persist
the release helpers with actual mergeM and a fresh inventory, run the original
guarded adapter rehearsal, then authorized prepare/prepublish/publish and the
official verifier. Only then perform the single visible manual refresh and
inspect the sanitized failure stages or successful advanced saved timestamp.

Production changed by this task: **no**. No original /app source edit, merge,
lease or publication has occurred. Status: **A verified, B frozen; B CI and
live release/recovery pending**.


## Production advance: replacement source R2

Before any merge or original-environment write, the production branch advanced
to af9caabb3119aeb9cf450af0a8b89d376cef8c74 (normal merge PR1923). Its parents
are77e4acf33e8f5432d97bda5d7208983f0fadc599 and ece0b24b0978c5064c4e605b577ad2722158f56a.
It adds reviewed delayed Salla creation-choice enrichment and its tests/workflow
plus a new frozen intent. Root fetched and compared both deltas: the10 original
non-intent source paths have no overlap with that production delta. The old
PR1925 now conflicts at the release handoff; do not merge or publish it.

Old B nevertheless finished all29 checks:23 success,6 conditional skips,zero
neutral/pending/failure. Both actual clean-clone host jobs ran and passed with
the recorded source/runtime/artifact identity. Those are old-candidate evidence
only. All old A/B commits and the documentation checkpoint remain recoverable;
no frozen commit was amended, rebased or force-pushed. No old candidate changed
production, created a lease, or changed /app.

Replacement worktree: mezan-source-read-recovery-r2.
Replacement task branch: codex/financial-source-read-recovery-20261114-r2.
New reviewed base J2:af9caabb3119aeb9cf450af0a8b89d376cef8c74.
Root applied only the original source-A non-intent patch over J2, verified the
nine runtime/test/workflow file blobs remain exactly those independently
reviewed and tested before, and verified J2's tracked intent is untouched.
The existing AGENTS.md bytes are unchanged. This status is the only provenance
update in the replacement source. All PR1923 files remain exactly at J2.

Next: run fresh combined affected tests on this integrated J2 worktree, persist
new source A2, and create a replacement Draft PR. Close PR1925 as superseded
only after linking the replacement. Run exact A2 CI, a fresh governed two-build
and new intent-only B2; do not reuse old intent bytes or runtime ID. Then all
B2/normal-merge/rehearsal/guard/live verification gates still apply. The other
release may now be active: freshly inspect original guard before any /app work.

Production changed by this task: **no**. Current state: **R2 integration in
progress; old PR1925/A/B are superseded and must not deploy**.


## R2 fresh integrated verification

Root's fresh combined run on the actual J2 working tree exited0:541 backend
tests passed in28.55s, with the same two Pydantic deprecation warnings and no
skips/failures. It used Motor3.7.1/PyMongo4.18.3 and the exact combined13-suite
command recorded earlier, now in mezan-source-read-recovery-r2. The governed
frontend rerun exited0:115 tests/2 suites passed in18.55s, using the same two
suites and the existing source-read-recovery dependency tree via --modulePaths.

Independent read-only R2 integration review found no concrete incompatibility.
It independently confirmed all nine reviewed runtime/test/workflow blobs match
old sourceA, zero overlap with the11 PR1923 paths, and exact preservation of all
PR1923 files including its intent blob f44168176bc3a995776f71e4aa61ea4ee9509366.
PR1923 enriches products and raw_by_source.salla_direct.items options through
the existing owner-fenced writer. The financial refresh retains whole canonical
containers and derives cost items from raw.items. Ordinary rows retains its
full document, and no writer enters the new read-local CSOT scope.

Current state: **R2 source locally verified and independently reviewed; remote
A2 checkpoint, replacement PR and all new release gates are next**. No original
/app source change, lease, refresh, or publication has occurred in this task.
