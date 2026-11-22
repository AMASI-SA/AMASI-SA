# Operational balances cold-load latency — source WIP

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
