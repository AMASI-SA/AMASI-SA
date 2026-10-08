# Shipping delivered serialization experiment

This branch is a benchmark artifact, **not a production fix or a merge candidate**.
Its parent is PR-B #1300 at `617858e5d327d7c1cd898e73ef6aa12e726e567f`.
Only this directory and `.github/workflows/shipping-delivered-benchmark.yml`
may differ from that parent. No source, sync writer, operational owner,
Release Guard, Intent, migration or customer record is changed.

## PR-A final disposition

PR #1302 remains unchanged at `973fa9b6cdbcb00ca2da12c43fbadb3b4661fc8c`,
base `128bfc1c2b4670d853a8bce33f15a667d2fdb120`. The user accepted functional
completion: real Mongo 48 pass / 0 skip / 0 fail; Frontend 42 pass;
governed build/reproducibility and all four required checks pass. Exact patch:
9 files, 335 insertions, 88 deletions; SHA256
`8b9e1e70d236e5a81a97debbb01039bd46dc6655ab815fe36a515de96f4732e7`.
An extra blank line at EOF is retained rather than changing the reviewed HEAD.
No merge or deployment is authorized. Reversal after any separately authorized
merge would be an independently reviewed revert of PR-A only, not data repair.

## Execution contract

Linux, CPython 3.11, MongoDB 8.0.12, real single-node replica set PRIMARY.
The environment probe fails on mismatch. Only the disposable localhost URI
is accepted. Each independent trial has its own GitHub runner and Mongo process;
designs within that trial run sequentially on the same host. Trials do not
share CPU or database state. This is not a multi-replica durability/failover study.
Synthetic fixtures exercise real Mongo transactions, inventory and ASGI
mark-ready routes. Provider/authentication boundaries are test doubles.
No Salla request or production test is allowed.

Three treatments are compared:

1. Current baseline (known unsafe negative control).
2. Every exercised canonical writer uses the existing merchant owner fence.
3. Mark-ready performs an actual modifying write on the canonical order in
   its existing transaction, retaining the existing merchant owner fence.

Treatments are temporary in-process test adapters, never patches to runtime
files. An extension to *all* writers in production is not implemented here;
coverage of the exercised writers must be read from the safety output.

## Interpretation

Safety needs deterministic interleavings, not merely zero failures in a load
run. A delivered write committed before mark-ready's canonical serialization
point must prevent any committed inventory, piece, workflow or event effect.
When mark-ready serializes first, a concurrent canonical writer may wait;
this is local commit ordering, not precedence based on a remote Salla clock.
Late/out-of-order provider payload policy and post-commit external issuance
are separate problems; this experiment cannot claim distributed atomicity.

Rejected requests must preserve business state and make zero provider calls.
The baseline should expose the known race. A green benchmark CI therefore
means its assertions (including the unsafe baseline control) held, not that
baseline or PR-B is safe to merge.

Raw results distinguish successful operations, rejections and failures.
Percentiles need their sample counts, and closed-loop/burst measurements are
not open-loop SLO evidence. Do not hide retry latency or pool/admission wait.
CPU/I/O counters are marked unavailable if container permissions prevent
collection; no invented zeros. Trial order, concurrency, document population
and repetitions must be reported alongside the measurements.

No performance improvement or material-regression threshold is assumed.
Compare matched workloads to the measured baseline and report absolute and
relative changes. Hosted-runner results cannot establish production capacity
or production p99; a missing workload or inadequate tail sample remains a gap.
No change is applied to PR-B based solely on this experiment.

## Preserved experimental failures

- Run `37841719093`: safety 60/0 skipped passed, but smoke failed before any
  measurements because fixture cloning duplicated tenant-wide configuration.
  The fixture was corrected; no runtime fix or assertion bypass was used.
- Run `37842265908`: safety and smoke passed. The shared-owner physical
  100-order same-merchant cell reached its unchanged 90-second request deadline
  with only 39 acknowledged transitions. This is a measured load failure,
  not a fixture result to discard. Subsequent collection records failed cells
  and continues the matrix, then reports failure rather than claiming PASS.
  Duplicate `different`-topology hot-order cells are omitted because one hot
  order still belongs to one merchant; the same-merchant hot cases remain.

## Evidence and rollback

GitHub Actions uploads environment proof, dependency versions, raw timings,
race assertions, resource samples and Mongo logs even on failure. Treat each
run as tied to its exact Git SHA; preserve failed attempts and their causes.
No lease, Prepare, Prepublish, merge or deployment is involved.

Rollback of this experiment is simply to stop using its branch/workflow.
Both application PRs remain unchanged. An eventual production fix needs
separate design approval and a fail-closed rollback strategy that does not
silently restore the known mark-ready race. No backfill is part of this work.
