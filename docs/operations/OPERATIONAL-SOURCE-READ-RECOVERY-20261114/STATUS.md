# Operational source-read recovery — 2026-11-14

## Current outcome — one successful visible manual refresh observed

PR #1928 is merged and published. Its official three-probe verifier and final
120-file preservation check passed. The earlier post-publication attempt timed
out, but the single prepared diagnostic attempt in the next continuation
advanced the saved financial timestamp from 14 November 2026, 18:22 to
**14 November 2026, 23:09 Asia/Riyadh**, with the page visible and no new
timeout/source-failure warning or diagnostic event. The covered source-reader
cancellation defect remains fixed and verified. One successful live outcome
is demonstrated; the earlier timeout and any intermittent latency cause remain
unexplained. No further refresh, code change or publication is needed merely
to repeat this result. See the final checkpoint below.

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


## R2 frozen intent checkpoint — 2026-11-14 18:39 UTC

Canonical current candidate is draft PR #1928, https://github.com/AMASI-SA/AMASI-SA/pull/1928, on `codex/financial-source-read-recovery-20261114-r2`. PR #1925 is closed unmerged and superseded; its frozen heads are preserved and must not be deployed.

- Current production base J2: `af9caabb3119aeb9cf450af0a8b89d376cef8c74` (Salla delayed-choice enrichment from PR #1923 retained).
- Source A2: `d41cd73132793209d13758ef07b2eda4fcfe018a`; tree `fbb96bf50748af391abd5c9f4c4d253e2ac13423`; parent J2.
- Intent-only B2: `db6971b59b12bdda3a3585712ca881ac22d6bc0f`; tree `355fdae884cf4a849b0acdf1c29081730556c6b0`; parent A2.
- A2 to B2 changes only `release/release-intent-v5.json`. Local checkout is aligned and clean.
- Exact intent blob `097b7fa75d0ab840d1c8396d98ccfb279e64f777`; 1,308,609 bytes; SHA-256 `57ff9aacd5490ce42605ee22d9ab9cf1f2e8c6855fc9e2664266992438efd279`.
- Runtime `rg5-12003f05673102a08c649a74e75e894372796da07bd75ada680a7f07aec65951`.
- Frontend artifact tree `d6bd189f78c5bb7467fa0b0c7a8edc2e0c36f8d22f6b5ab23242b0a9a0dc25bd`.
- Retained two-build proof SHA-256 `156ad123b2c9fa009804a40f58ef3e51ba028337af065380b484beca02f506d4`.

### Completed R2 evidence

Fresh root integration: 541 backend tests passed in 28.55 seconds using Motor 3.7.1/PyMongo 4.18.3, and 115 frontend tests in two suites passed in 18.55 seconds under governed Node 22.23.2/Yarn 1.22.22. Independent review found the nine code/test/workflow blobs identical to the prior reviewed patch, no path overlap with PR #1923, all PR #1923 files and its source intent preserved, and no blocking interaction with Salla canonical item enrichment.

All exact A2 checks settled: 21 successful and six conditional skips across 27 checks. Full operational backend: 4,799 passed plus 16 shipping tests and 18 subtests; frontend: 783 tests in 52 suites; browser/mobile/visibility: 14 passed; stock: 4,513 passed and seven subtests; both CodeQL languages passed. The operational backend installed the production Motor 3.7.1/PyMongo 4.18.3 pair and ran the actual-Motor cancellation regressions.

Governed local source build exited zero, as did the official frontend verifier, freeze-intent and candidate intent loader. Local and exact-A2 CI artifact intent bytes are identical. Readiness run 43626990075, artifact 14125584742, ZIP SHA-256 `e5c7ab19caaa805e6b80ea4357eaadd4d6673b5d826c8954b0310daefbc05c39`. No fake GitHub markers were supplied; the documented source-candidate bootstrap flag was used.

### Original environment and pending release

No original `/app` mutation, release lease, publication or new live financial refresh has been performed for this task. Fresh read-only original status/inventory is being obtained while B2 CI runs. The previous full inventory was 120 files, but new PR1928 templates deliberately keep the count unfilled until a fresh final review.

Four new `pr1928_*` release templates are retained beside unchanged PR1925 templates in `release-runners/`. They use source A2, base J2 and actor `codex-financial-source-read-1928`, retain all guard/preservation/provenance gates and the official verifier's no-wrapper-timeout/interrupt-join rules, and require final B2/M2/tree/runtime/intent/count/evidence inputs. They passed static and independent exact-substitution review; none has been executed. These helper files and this handoff branch are documentation artifacts, not part of the frozen release candidate.

Next: exact B2 checks including both actual clean-clone adapter jobs; fresh production-base check; normal M2 merge with parents [J2, B2] and tree B2; fresh inactive guard; configured and independently reviewed original helpers; original governed adapter rehearsal and actual exit; prepare/prepublish; authorized free publication; official three live probes and inactive guard; then one visible intentional financial refresh. Another draft campaign PR #1927 may advance production: preserve frozen candidates and create a fresh candidate if that happens before our merge. Do not overwrite or interrupt another release.

The demonstrated cancellation defect is fixed in the candidate; the original production query latency cause remains unmeasured. Generic profit callbacks inherit CSOT but not strong cursor ownership. No production database credentials, customer/financial records, index changes, deadline increases, provider sync or financial transactions are authorized by this verification. Claim live recovery only after a successful manual refresh advances its saved timestamp.


Fresh original read-only status returned at 18:39:45.920340 UTC: HEAD `af9caabb3119aeb9cf450af0a8b89d376cef8c74`, tracked files clean, 120 complete untracked files and guard `{"active": false}`. This task made no original changes. Independent B2 review also verified its single parent, sole intent path, exact tree/blob and byte equality across Git, local intent, CI intent and sole ZIP member.


## R2 normal merge and configured helper checkpoint — 2026-11-14 18:49 UTC

Root freshly re-read exact B2 checks: all 29 completed, 23 successful and six declared conditional skips, no pending/neutral/failure. Both actual clean-clone host logs were independently reviewed by the agent and root: readiness run 43627775151, Node20 job132547644707 and Node24 job132547644724, exact source/runtime/frontend identities and isolated backend/frontend artifact proofs. B2 operational backend again passed 4,799 plus16shipping/18subtests, frontend783/52, browser14, stock4,513/7subtests; Python and aggregate CodeQL passed.

Root freshly confirmed production J2, exact B2 PR head and mergeability, marked PR #1928 ready, rechecked production J2 immediately before normal merge, and merged with expected head B2.

- Final M2: `3e9dc3b25e41c663e001822a666323c802e5ec5a`.
- Exact ordered parents: [`af9caabb3119aeb9cf450af0a8b89d376cef8c74`, `db6971b59b12bdda3a3585712ca881ac22d6bc0f`].
- M2 tree equals B2 tree `355fdae884cf4a849b0acdf1c29081730556c6b0`.
- A2 to M2 changes exactly `release/release-intent-v5.json`. GitHub and freshly fetched local Git objects agree; frozen B2 working tree remains clean.
- Fresh original status at18:46:41.540788UTC: HEAD J2, trackedclean,120 full untracked files, guardinactive. Unsaved Untitled-1 and original terminals1/2 remain untouched; temporary Terminal3 is task-owned.

The three PR1928 wrappers now contain exact B2/M2/tree/runtime/intent and fresh count120. Gate EVIDENCE deliberately remains unfilled until an actual successful original rehearsal produces it. Root reviewed the complete four helper files; independent exact-six-substitution review passed with matching hashes and no blocking issue; the preservation helper is byte-identical and all executable logic is unchanged. No original /app mutation, lease, publication or new financial refresh has occurred yet. Continue only after that review, stage exact reviewed helper bytes in a private temporary directory, and run the original governed build rehearsal. All later preparation, publication and official verification gates still apply.

Emergent's former tab28 became unresponsive to the documented focus API; a new temporary tab37 restored the same signed-in Salla Analytics project through visible navigation. It is on Manage/Overview with Republish visible. No paid chat/Build/Ask-agent action or credit purchase was used. Current visible published version remains100th publish `5a7a310`; newer explicit platform success must be observed for this release.


## Original R2 rehearsal succeeded — 2026-11-14 18:52:42 UTC

The original guarded helper fetched and fast-forwarded `/app` from J2 to exact M2, checked the exact normal merge, intent bytes and complete fresh untracked manifest, then ran the real `yarn build` adapter from `/app/frontend`. The actual build started18:51:39.359831UTC and ended18:52:42.293406UTC, elapsed62.933585 seconds, exit0, problemnull. It materialized the exact reviewed runtime identity (SHA-256 `5c040d9a31bf14a85d81eba4a2041b6d1402762482c26fe8e06d1201fc2bec50`).

The wrapper completed at18:52:42.482984UTC with phasebuild,readytrue, exact A2/B2/M2/tree/runtime/intent/base, count120 and lease_createdfalse. Root separately read the private actual build-result, identity-match, preservation and guard records: before/after counts120, changed_paths[], preservedtrue and guardinactive. Intent digest and M2 ordered parents were rechecked directly against saved evidence.

- Successful private evidence: `/tmp/mezan-pr1928-original-2e86crex`.
- First reviewed helper staging: `/tmp/mezan-pr1928-runners-uf4jkioo`, exact bytes from handoff `bdc94f867a23ff64c774f7a139b2e4bf8a821b0b`; staging at18:50:51UTC confirmed originalJ2,trackedclean,120 files and inactiveguard.
- Gate EVIDENCE is now filled with that actual successful rehearsal path. Independent review confirmed exactly one line changed against `bdc94f86`; all other helpers are byte-identical and no configuration placeholders remain. Gate SHA-256 `adb97abc5a513aa4263d4dfff5f7e3e6a5e02cfdfae8655a860b20d4def31667`,11518bytes.

Next stage these completed reviewed bytes privately, run prepare and the preservation helper, then prepublish and preservation, promptly perform the already authorized free Republish, wait for a newer explicit platform completion, and run the official verifier once through the no-timeout wrapper. Do not rebaseline the120-file manifest, interrupt the verifier, touch original terminals1/2 or the unsaved Untitled buffer, use paid chat/Build/Ask-agent controls, or trigger financial refresh before official identity verification. No lease or publication has yet occurred for this task; the live manual refresh remains pending.


## Original publication completed; official verification running

Final fully reviewed helpers were staged from `abff26067a8f926c4c903829b330fa516347c6d7` into `/tmp/mezan-pr1928-runners-1083fdjp` at18:56:21.863842UTC. Staging rechecked original HEAD M2, tracked-clean,120 untracked files and inactiveguard. The prior helper directory was preserved.

Prepare completed18:57:25.565951UTC with actualexit0, elapsed2.645402seconds and an owned active protocol5 lease for actor `codex-financial-source-read-1928`, exact A2/J2/M2/runtime. Prepare stdoutSHA256 `1afdcd9219c59cb0f61a3cb404289429d8880c8e6028d35cd35c2ffee78c67ae`. The separate after-prepare preservation passed at18:57:25.820059UTC,120 files preserved.

Prepublish completed18:58:20.037505UTC with actualexit0, elapsed3.393089seconds, ready_to_publishtrue and the identical owned lease. Prepublish stdoutSHA256 `be3393e35ea17893b451fc01feb86148854df80423b164a9f2b18c31d0995680`. Its separate after-prepublish preservation passed at18:58:20.248301UTC,120 files preserved.

Root used the original project's Manage/Overview controls, opened Republish, observed Free republishes and submitted Publish changes exactly once. No paid chat/Build/Ask-agent/credit purchase was used. The platform progressed through Workspace ready, Changes packaged, cloud installation and Health Check Successful, then displayed a NEW explicit **Deployment finished Nov 14,10:08PM (Asia/Riyadh)** and version **3a22e2d**. This is the current publication attempt; do not submit it again.

After that explicit completion, root started the official verification exactly once from task-owned original Terminal3:

`python -B /tmp/mezan-pr1928-runners-1083fdjp/pr1928_original_verify.py --evidence-dir /tmp/mezan-pr1928-original-2e86crex --deployment-sha 3e9dc3b25e41c663e001822a666323c802e5ec5a --actor codex-financial-source-read-1928`

**Current state: official verification is in progress.** It has no external timeout and must not be interrupted, detached or restarted. Do not change original `/app`, clear another or this lease manually, publish again, or run a financial refresh while it is active. Wait for its actual `28-verify.result.json`/summary, all three expected live probes, matching runtime/frontend identity and a fresh inactive guard, then run the separate preservation helper with labelafter-verify using the original120-file baseline. Only after that make one visible intentional `تحديث المصادر والأرصدة` attempt and observe its settled saved `as_of` and sanitized stage logs. No financial recovery is claimed at this checkpoint.

Production branch watch has remained M2. Frozen source A2, intent B2 and M2 are unchanged. Original terminals1/2 and unsaved Untitled-1 remain untouched. Browser tab37 is the current working Emergent management tab, tab27 the original code-server, tab21 the financial page (reload it once after official verification to load the published frontend before the manual test). Private evidence stays at `/tmp/mezan-pr1928-original-2e86crex`; never publish raw manifests, customer/financial records or secrets.


## Official live verification passed; one manual refresh failed

This checkpoint supersedes the preceding in-progress state. Production changed
by this task: **yes**, through the already recorded single free publication
of PR #1928 / M2, platform version `3a22e2d`, explicitly finished at
22:08 Asia/Riyadh on 14 November 2026.

The original official verifier started at 19:09:52.434557 UTC and finished at
19:22:36.992638 UTC, actual exit 0, problem null, elapsed 764.558089 seconds.
All three live probes matched source A2
`d41cd73132793209d13758ef07b2eda4fcfe018a`, deployment M2
`3e9dc3b25e41c663e001822a666323c802e5ec5a`, and runtime
`rg5-12003f05673102a08c649a74e75e894372796da07bd75ada680a7f07aec65951`.
The official result reported 582 frontend request records; these are request
records, not a claim of 582 distinct files. Root separately inspected the
actual command result, official JSON, its digest and the fresh inactive guard.
Verification stdout SHA-256:
`0dbcb38d4cc6104cd48ef41dcd66dd9e7103035bb256ee0368c666a66b1c3a1b`.
The official guard closed its matching owned lease. No verifier was interrupted,
repeated or externally timed out.

The separate final preservation check completed at
19:23:29.109552 UTC: all 120 complete baseline untracked files were preserved,
with no changed paths. Original terminals 1/2 and unsaved Untitled-1 remain
untouched. Task-owned Terminal3 was left idle. The frozen R2 worktree remains
clean at B2; helpers and private evidence remain at their recorded paths.

### Subsequent production merge preserves the fix

Production Git advanced at the 19:18:49 UTC observation to
`324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`, normal merge PR #1929.
Its ordered parents are M2 and `93efe593ebe2c401763c5d8faa4ba8547a8a8016`.
Root fetched the actual commit; independent full-tree review verified that all
nine PR #1928 code/test/workflow blobs and modes are unchanged. The four new
paths are the campaign operating-cost code, its test, its task status and its
release intent. This is a later descendant of our published fix. Do not reset,
overwrite, re-release or clear a lease belonging to that other conversation.
The watch's final observation at 19:34:43 UTC found no further Git advance;
the read-only watch has now ended. This paragraph
does not assert that the later candidate has or has not been published.

### Single visible functional attempt

The old financial tab had closed. Root opened a fresh financial summary tab
at the verified URL
`https://mezansalla.com/operational-balances/financial-position?view=summary`.
Its loaded frontend script was `/assets/index-BliItB6V.js`, matching A2.
Saved-report and snapshot-bound forecast reads settled first. Both fixed
diagnostic console-label baselines were empty, and the saved date was
`14/11/2026, 18:22` Asia/Riyadh.

Root clicked `تحديث المصادر والأرصدة` exactly once, observed start
19:26:05.331 UTC (22:26 Riyadh). The page remained visible throughout the
attempt and kept the dated previous report visible while the button was
disabled. No second refresh was performed. Root initiated no separate provider
sync or financial transaction; the timeout alone cannot prove that no admitted
internal operation had an effect.

At 19:27:34.715 UTC the existing allowed browser log interface recorded:

`operational_refresh_timeout_stage_timings Array(22)`

No source-specific 409 label appeared. Frozen A2 emits this exact timeout
label only for status 408 with `operational_refresh_timeout`; therefore the
code path is identified. Its backend handler covers an escaping TimeoutError
inside the timed work region, so the label alone does not prove which timer or
component expired. The raw HTTP response/status was not separately
captured and must not be described as directly observed network evidence.

At the settled 19:29:24.824 UTC observation, the button was enabled, the page
was visible, the saved timestamp was still `14/11/2026, 18:22`, and the warning
was exactly:

> تعذر تحديث المصادر؛ تُعرض آخر أرصدة محفوظة بتاريخها. انتهت مهلة التحديث؛ احتُفظ بآخر لقطة ناجحة

**Live recalculation failed.** A successful publication, a readable saved
report, or a re-enabled button is not evidence of successful recomputation.
Do not call observed browser wall time a server or database-query duration.
The configured 85-second backend request bound is not a measured query time.

### Available diagnostics and concrete access limits

The permitted browser log API exposes only rendered `Array(22)`, not the
individual rows. An ordinary native attempt to open Chrome DevTools displayed
`DevTools not allowed on this page` and
`Your organization has blocked the use of DevTools on this page`.
Root dismissed the notice and did not retry, bypass it or use another protocol
to access the blocked tools.

The existing published-version Manage / View Logs surface was then inspected.
It exposed only its latest 100 entries, with timestamps about
19:32:13–19:32:31 UTC, after this attempt. No matching
`operational_refresh_stage` or refresh-POST timing lines were present. No
earlier-time selector or pagination was exposed in that view. The existing
backend emits sanitized INFO lines of the form
`operational_refresh_stage stage=... duration_ms=... outcome=...`, but no
request/owner correlation identifier; overlapping attempts would require an
explicit attribution qualification. These are stage timings, not database
query-plan measurements, and nested stages must never be summed.

The platform's Monitoring page reports aggregate performance/resource pressure
over 24 hours. It does not identify this attempt's slow stage or establish a
missing index, pool contention, data volume or CPU as the cause. No resource
upgrade, credit purchase or paid agent action was performed.

Production database access remains blocked by the previously observed
`Session Ended` viewer and its one unsuccessful recovery. No further automatic
authentication retry, token/cookie inspection, credential workaround, owner
guess or use of the preview database as production was attempted. No production
query plan or database execution duration has been measured.

### Exact next safe action

Preserve this remotely recoverable documentation checkpoint and PR #1928.
The next useful step is restoring the existing production database diagnostic
session through the owner's normal visible management flow, using the
documented browser-auth handoff where needed. Do not ask for passwords, tokens
or connection strings in chat. Restoring this viewer is only a diagnostic-access
step: it will not recover the lost 22-row trace or prove that the database caused
the timeout. Once the production session is actually usable,
perform bounded read-only diagnosis with verified owner scope and sanitized
metadata; establish the slow stage/query before changing source, indexes,
deadlines or financial formulas. No additional refresh should be triggered
merely to resume or while waiting for access.

If a future explicitly justified diagnostic attempt is needed, prepare the
existing permitted backend log view first and capture its narrow stage lines
promptly before the 100-entry tail rotates. It must remain one visible,
intentional action without background polling or automatic retry. Any source
change requires a fresh reviewed source/intent candidate on the then-current
production base; never amend the frozen A2/B2/M2 or reuse this completed lease.


## Successful prepared continuation — 2026-11-14 23:11 Riyadh

The owner asked to continue after the authentication-blocked handoff. Root
re-read the skill, canonical status and fresh Git evidence: handoff
`48529aff4caec37197f3ff20acf622ef2e989668` was clean and matched its remote
branch, R2 remained frozen at B2, and production Git remained the reviewed
descendant `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`. The database viewer
was not present after the prior handoff; its restoration was not assumed or
retried. No production credentials, customer records or financial amounts were
read or copied.

The current management UI showed a subsequent published version `75ee080`,
with our `3a22e2d` as the previous published version, and no publication in
progress. The current production Git intent identifies source
`fcb827f556c40bebd843d8c9d85fedc0b60cf8fa` and runtime
`rg5-4a98965dc9ad5922b4dbd25e5c960360af8b09f8e5590cced0b87c8c8a0f07a2`.
Its frontend artifact tree is the same reviewed
`d6bd189f78c5bb7467fa0b0c7a8edc2e0c36f8d22f6b5ab23242b0a9a0dc25bd`.
These intent fields are Git evidence, not a newly executed official live
identity verification of the other conversation's publication. This task did
not publish again, touch `/app`, create/clear a lease or alter the frozen heads.

### Prepared normal log capture

Before considering another financial calculation, root proved that the
documented raw Playwright management actions and narrow DOM reads leave the
financial page's `document.visibilityState` equal to `visible`. No native AX,
tab-focus action, DevTools workaround or hidden-state access was used during
the attempt. Normal Overview / View Logs controls were the only log-access
path. A normal management-page reload was used during preparation, not as an
authentication retry.

An ordinary visible reload of the saved financial summary began at
20:07:47.905 UTC. It did not invoke the financial refresh POST, and it settled
on the existing 18:22 saved date. A later normal management log view showed a
fresh corresponding server record:

`2026-11-14 20:07:58,399 api_timing method=GET route=/api/operational-balances/financial-position status=200 duration_ms=8311.158 outcome=2xx`

The retrieved tail covered 20:07:53.967 through 20:08:09.683 UTC. This was a
successful freshness beacon for the permitted log view. Its 8,311.158 ms is a
server duration for that preflight GET, not a database query time, speedup
benchmark or duration of the subsequent manual refresh. Earlier unchanged
tails did not by themselves prove caching.

Preflight saved-report/forecast loading notices were absent and the refresh
button was enabled. The existing timeout-event baseline contained one rendered
`Array(22)` event with captured timestamp 20:02:04.656 UTC; it was treated only
as an existing baseline, not attributed to this new attempt. The source-event
baseline was empty.

### Actual observed result

Root made exactly one prepared diagnostic click at **20:09:30.395 UTC**
(23:09 Riyadh). The purpose was to capture the previously unavailable stage
evidence through the now-prepared permitted surface, not to blindly repeat a
failed refresh. The financial page stayed visible and showed the disabled
updating button while keeping the dated saved report readable.

At the first settled observation, **20:11:13.746 UTC / 23:11 Riyadh**:

- Saved calculation date advanced to **14/11/2026, 23:09 Asia/Riyadh**.
- The refresh button was enabled.
- No timeout, source-failure or paused-attempt notice appeared in the inspected
  fields, and no new fixed timeout/source diagnostic event appeared.
- The financial page was still visible.

The final observation at **20:11:55.230 UTC** confirmed the same fresh 23:09
saved date and settled controls. **One successful visible manual outcome is
demonstrated.** The 103.351 seconds from the observed start to the first settled
observation is an observation interval, not an exact refresh, server or Mongo
duration. No raw refresh POST status or stage-duration trace was captured.

Interim and terminal normal log reopens continued to expose the earlier
20:07:53–20:08:09 tail. No stage records for this attempt were returned. Do not
invent them or attribute the preflight GET duration to the calculation. This
success does not explain the earlier timeout or establish permanent resolution
of intermittent latency. No additional calculation or test is justified merely
to strengthen the success claim.

### Final state and continuation

The financial summary tab was marked as the user-facing result with the fresh
date. The original code-server tab and unsaved editor were preserved. The
browser completion summary was accepted; no further browser action is pending
for this successful check. Database diagnostic access remains unverified, but
it was not required to observe this successful calculation. The owner does not
need to restore that session merely to use the newly updated financial page.

No source-code change, new deployment, additional database-auth attempt,
provider sync or separate financial transaction was initiated in this
continuation. Existing demand-only behavior remains unchanged. The independent
read-only reviewer agreed that this one successful outcome is supported and
that no further refresh/test is needed now; latency root cause remains unknown.

The proposed one-line JSON serialization of already sanitized timing rows was
reviewed only as an observability contingency and **was not implemented**.
If a future failure requires investigation, continue from current production
and fresh evidence; do not automatically retry the refresh or resurrect old
leases. A future instrumentation change would require a new reviewed source /
intent candidate and the full repository release protocol. Current task status:
**published source correction verified; successful live refresh observed once;
intermittent performance cause unresolved, with no further change claimed**.
