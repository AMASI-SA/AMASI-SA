# Operational balances cold-load latency — R3 integration on current Production branch

## Current handoff: R3 preserves merged customer/inventory behavior

Repository: `AMASI-SA/AMASI-SA`. Current task branch:
`codex/operational-demand-latency-r3-20261122`.
Exact reviewed branch base N:
`24270ebdfdebb83282389308a2349cdb27e2b8e4`, tree
`6a906db6cf4fd3dda4b550483a0ef96067c6f1ed`.
This is the ordinary PR2074 merge, with ordered parents M
`06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35` and B
`447e83bfb95d2627877d46c7baa528e454a2e143`.
N is verified on the Production Git branch; that fact alone does not prove
the customer/inventory release is live. Its owner's coordinated host staging
and publication handoff remains separate and must be preserved.

The four performance fixes below are carried onto N. In the two common UI
files, integration keeps customer refund/collection actions, adjustment
badges, collection routing, permission gates and deferred-refresh handling
alongside the validated owner ledger's single-request path. The workflow
retains N's customer/inventory gates and adds the three real child-lifecycle
acceptance cases. The inherited N intent is unchanged throughout source work.

Old source A `6dcda12be98a68130954b42c704e209e03a43bcd`, tree
`96c53755ccb9630adb8a7a5bae437e6239041305`, remains recoverable in Draft PR2075
and `codex/operational-demand-latency-r2-20261122`. Its eleven exact-source CI
workflows all passed. That includes Operational Balance Web run45058886733
(backend6307 plus90subtests, demand96, frontend1519/95suites, browser and
canonical worker acceptance), and Release Readiness run45058886749 with two
identical governed builds. These are historical results for old A, not
acceptance of this combined source. No intent-only B was committed for old A;
its candidate intent/runtime/artifact must not be reused for R3.

Fresh R3 affected checks passed: **194 Frontend tests in 10 distinct suites**
(127 reader/report/API/hook cases and 67 Finance/Ledger/CashBoxes cases) and
**101 Backend tests**, all with zero failures, errors or skips. Backend checks
include all seven CI demand suites, the three unified customer/inventory
regressions and the native receipt/payment-allocation replay cases, using
only an owned temporary loopback Mongo replica. The new Frontend integration
cases prove bootstrap=1, selection=0 and ledger=1, no customer badges/actions
before the validated response, and exactly one customer-review request only
after opening the requested review. A foreign scope exposes no resource,
badge or action. All three upstream N Frontend integration tests remain.

Independent reviews approved both the unchanged runner/report/polling fixes
and the Finance/Ledger union. Source, tests and workflow pass diff whitespace
checks; the retained historical failing pytest XML deliberately preserves its
four original whitespace-only traceback lines. No test/source was removed to
make verification pass. Workflow bytes are exactly N plus the two lifecycle
selector/count additions. New evidence is in `evidence/r3-*`; historical
before/after artifacts retain their original bytes and scope.

The exact R3 source SHA and PR will be recorded in Issue1006 after
checkpointing, followed by fresh exact-source CI and a fresh original
two-clean-build candidate intent. These local checks do not replace the full
CI, build, host rehearsal or live acceptance. Do not freeze or publish the
historical old-base candidate.

The read-only `MANUAL-HOST-LATENCY-DIAGNOSTIC.py` now expects N. SHA256:
`0b608b157d3092a04f893637963ea864a8d9bfe98592fedb470488e4d7bcfc8a`.
Only its expected base and mismatch wording changed; it has not run on the
host. Its output explicitly does not establish Production database/log scope.
The earlier R2 review and file hashes below are historical; R3 review is
recorded separately. The old PR2075 host-rehearsal draft outside the repository
is disabled and obsolete, and must not be activated by filling only its B pin.

**Production changed by this performance task: no. No new merge, lease or
publication. First-visible Production latency remains unverified.** The user's
reported lower overall load is an invariant: no eager full-workspace refresh,
worker concurrency increase, financial calculation change, or permission/
source validation relaxation is introduced. Before any host rehearsal or
shared change, re-read Issue1006 and current Production, preserve PR2074's
serial host handoff, and use the unchanged original inactive-guard checks.

---

## Historical R2 integrated-source record (superseded base and release plan)

## Current handoff: four bounded fixes verified locally; exact-source CI next

Draft PR2075 targets the exact deployed base
`06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35` from
`codex/operational-demand-latency-r2-20261122`. Prior remotely recoverable
lifecycle checkpoint: `8920c8ed5bc5acc6643286256098e70e59695949`.
The following current state supersedes the initial WIP notes retained below.
The candidate's exact new SHA is recorded in Issue1006 after checkpointing;
do not substitute the prior checkpoint when freezing the intent.

The owner-reported tenfold delay is not yet measured in Production. Overall
system load is already lower, and that improvement must be preserved. Four
specific defects/delays have evidence-backed source changes:

1. **Child shutdown:** raw stdin reading prevents the reproducible SIGABRT and
   releases the sole runner promptly after publication. Parent EOF still
   terminates the child. Actual-child canonical parity and source preservation
   pass; the second queued job fell from2556ms to1619ms in the bounded fixture.
2. **Owner ledger:** bootstrap now leads directly to one ledger request.
   The full authorized resource comes from that response; no selection job
   precedes it. Unresolved responses cannot expose names, balances or actions.
   Employee/balance-only flows keep the existing selection path. Provider
   defaults, saved invoices, recovered receipts, advertising FX and independent
   full-period printing remain covered. No speculative permissions or backend
   contract changes were introduced.
3. **First report view:** an initial detailed/expected link requests only its
   visible view. Its validated source identity anchors later views. Summary,
   profit and reconciliation still require a real summary, retained once read.
   The anchor keeps only immutable identity/currency metadata, not detailed
   report payloads. Cancellation cannot anchor a response;401/403/409 hide all
   figures until an explicit retry. Snapshot variants are never substituted.
4. **Readiness detection:** ordinary preparation uses delays500/1000/500/500/
   1000/2000ms, then2000ms. It adds only two checks per cold attempt, with the
   same consumer, serial requests and cancellation. Server hints above2000ms
   still apply. A controlled result ready at2s is observed at2s rather than
   3.5s. Pending30s makes19 requests instead of17; no additional computation
   or ready-result refresh is scheduled.

## Integrated verification and practical limits

Root executed the combined final Frontend state: **196 passed,10 suites,
exit0,6.859s**, plus the integrated Backend lifecycle/original worker suite:
**17 passed,exit0,7.08s**. Persisted evidence is
`evidence/root-integrated-frontend.json`, `evidence/root-integrated-jobs.xml`
and `evidence/validation-summary.json`, including exact source-file SHA256s.
Independent observed suites were90 ledger/neighbor cases,179 report/neighbor
cases, and78 reader/visibility cases; these overlap and are not summed.
The report-neighbor run was observed in agent tool output only; it is not
misrepresented as a separately retained raw log. Root's persisted integrated
run includes all38 final ApprovedReports cases.

Each changed behavior has a failing baseline or deterministic reproduction.
Independent reviews of runner, owner ledger, report binding and reader cadence
passed. Root caught and corrected a returning-summary reread regression before
the integrated run. `git diff --check` is clean. Local Frontend verification
used Node24.22.3 and the existing exact package.json/yarn.lock dependencies;
the governed Node22.23.2 build and exact-source CI are still required.

The local child benchmark is one before/after fixture, not a Production speed
percentage. The first home publication varied1144→1395ms while report
publication varied1094→1034ms; the shutdown fix mainly removes the queued-page
tail. The initial detailed/ledger changes establish one fewer full preparation
stage. Polling also has an explicit network tradeoff: in the controlled100ms
response model, ready2.2s displayed3.9→2.4s, but a later5.1s completion displayed
6.0→6.2s because the two extra responses add round trips. No universal timing
guarantee is claimed. The rejected five-second fast-poll window and a verified
immediate worker wake are retained as evidence, not implemented changes.

`MANUAL-HOST-LATENCY-DIAGNOSTIC.py` is a reviewed read-only host helper, pinned
to deployed M. It reads at most24 job-metadata rows through the existing named
index and bounded numeric log samples. The original guard's lock is respected
through a five-second bounded child status call. No endpoint/demand/worker
wake, index creation, source/output, lease or financial write occurs. Its
static review and pure sanitization checks passed; its main has NOT run on
the host. Host logs/database scope are explicitly unverified as Production.

Next: checkpoint this complete source candidate, inspect exact-source CI and
original A/B reproducible-build intent artifact, then freeze a new intent-only
B without changing source A. Re-read remote Production/Issue1006 and the
original host guard before a host rehearsal or later release step. The actual
host diagnostic and isolated adapter rehearsal may be handed to the owner
because no usable live browser/terminal session is available here. Existing
publication authorization persists; no renewed permission is needed.

**Production changed by this follow-up: no. No merge, new lease or publication
has occurred. First-visible Production acceptance remains unverified.** Keep
the completed PR2069 release closed, retain its backups, and keep PR2074 and
all unrelated work separate. Do not restore broad eager work, raise worker
concurrency, or weaken financial/source/actor/permission checks.

---

## Retained initial checkpoint record

## Scope and current state

The owner reports that published PR2069 greatly reduced overall system load,
with no hanging or memory pressure, but operational-balances pages now load
roughly ten times slower. The target is faster first useful operational reads
while preserving the bounded isolated computation which produced the load
improvement. This is not a memory-pressure investigation or legacy /reports
rewrite.

Repository: AMASI-SA/AMASI-SA.
Branch: codex/operational-demand-latency-r2-20261122.
Exact deployed base M: 06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35.
Original publication proof is preserved on evidence checkpoint
1dd476b69fe0ca89a89ff9a1c1da5d2dfc646ecf, including original verified=true,
checks=3, exact M/source/runtime/artifact and final guard active=false.
The original publication is complete. Do not replay its prepare or publish.

This checkpoint is source WIP, not a frozen source A or reviewed intent B.
No new source is merged or published and no new lease is held by this task.
Production changed by this follow-up: no.

## Confirmed defect and first verified slice

The child watcher blocked in sys.stdin.buffer.read() while the supervisor kept
stdin open. At ordinary interpreter shutdown its daemon thread held Python's
buffered-reader lock. Both nominal exit0 and exit1 became SIGABRT (-6), with
the exact _enter_buffered_busy fatal error. The sole global runner remained
held during this post-publication delay, slowing the next requested page.

backend/demand_read_runner.py now drains the raw stdin file descriptor using
os.read() until EOF. Parent EOF still immediately exits2, the daemon and
one-child isolation remain, and no lease, financial, authorization, cache
identity or source-revision logic changed.

backend/tests/test_demand_read_runner_lifecycle.py adds three actual subprocess
cases: natural exit0 and exit1 while parent stdin remains open, and non-EOF
data followed by EOF while a child is busy. The test disables its own core
dumps. The workflow explicitly includes this file and a minimum of three
collected cases in .github/workflows/operational-balance-web.yml.

## Evidence and verification

Artifacts in evidence/ are from a synthetic isolated loopback replica with
200 suppliers and 5,000 movements, never production data or requests.

| Check | Result |
| --- | --- |
| Lifecycle tests against unchanged original | Two expected SIGABRT failures; EOF case passed |
| Lifecycle tests after patch | 3 passed |
| Independent lifecycle rerun and code review | 3 passed; no blocker |
| Root integrated lifecycle and original demand jobs | 17 passed, exit0, 7.08s |
| Actual-child home/report result parity | Passed |
| Canonical source unchanged | Passed |
| Existing departure and supervisor cancellation cases | 3 passed |
| Workflow YAML, bash blocks, gate AST and collection | Passed; exactly 3 new cases |
| git diff --check | Passed |

Root integrated command, from this worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
PYTHONPATH=backend:backend/tests \
DEMAND_READ_TEST_MONGOD=/workspace/scratch/36b1bc151c92/mezan-test-mongo/bin/mongod \
/workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q \
  backend/tests/test_demand_read_runner_lifecycle.py \
  backend/tests/test_demand_read_jobs.py --tb=short
```

The fixture auto-starts its own loopback replica and owns its temporary UUID
database. The external paths above describe this execution environment; they
are not production commands. The fixture also supports the repository CI
environment. Existing pytest-asyncio configuration and fitz deprecation
warnings did not fail the tests.

| Local actual-child measure | Original | Patched |
| --- | ---: | ---: |
| Home child exit | -6 | 0 |
| Report child exit | -6 | 0 |
| Home claim through runner release | 2542ms | 1602ms |
| Report claim through runner release | 2434ms | 1496ms |
| Home publication through runner release | 1399ms | 207ms |
| Report publication through runner release | 1340ms | 462ms |
| Second report queue time | 2556ms | 1619ms |
| Ready report HTTP response | 7.26ms | 7.48ms |

These are one bounded before/after run, with 50ms publication observations.
They establish removal of a failing shutdown tail, not production percentages
or a guarantee of first-paint latency. First home publication varied from
1144ms to1395ms; report publication from1094ms to1034ms. Cold interpreter,
imports and database startup and frontend readiness polling remain.

## Remaining work and invariants

First-visible latency recovery is PARTIAL, not complete. Inspect serial
selection-to-ledger and summary-to-detail work with current permissions and
snapshot linkage preserved. Do not restore whole-workspace eager calculations,
raise computation concurrency, weaken actor/source/permission fences, merge
financial reports with different option flags, or extend stale results merely
to make timings smaller. The owner reports that pages eventually load; absent
worker infinite202 resilience is a separate finding, not this defect's proven
cause.

Next safe action: review the smallest evidence-backed first-visible
optimization, run its focused red/green and affected checks, update this
checkpoint and Issue1006, and create a reviewable follow-up PR. Only after all
source work is complete may a new exact source A and intent-only B be frozen
with the unchanged original protocol-v5 build/rehearsal/verification gates.
No live latency acceptance has been obtained: the cloud browser could not open
a usable page, so these are source and local fixture findings only.

Keep other task PR2074 and all unrelated files separate. Preserve the prior
host backups /tmp/mezan-pr2069-preserved-pls7dhb7 and
/app/.git/pr2069-stage-takiu9ut. Never clear another task's guard lease.
