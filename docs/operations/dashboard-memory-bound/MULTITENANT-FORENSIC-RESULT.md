# P1253 multi-tenant latency forensic

Application SHA: `bd8e1a4c377e61544b5cfe0f71f573c71abca89f`.
TREE: `73cf78b1b170fc37458ccc39ec29e6caaab432e1`.
[Linux run](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37240958761): SUCCESS.
Harness commit: `fcefcd4e96d55fc5f66220906d41b2c526a934ee`.

The workflow copied its diagnostic harness outside checkout, then checked out
the exact approved SHA with clean source. Python 3.12, Mongo 8.0.12, synthetic
loopback database. Same runner and fixed four-tenant dataset; 100,000 independent
orders per tenant, different monetary amounts per tenant. Sequential fresh
control/profile processes. No competing benchmark/test job on this runner.
One sample per condition; host jitter is not eliminated by this design.

## Finding

The dominant shared resource is the Python event-loop thread performing
synchronous calculations, SQLite operations and repeated buffer encode/decode.
Private tenant data does not make synchronous work parallel.

Three tenants consumed **80.036 process CPU seconds in 79.134 wall seconds**.
Their event-loop work accounted for **76.446 CPU seconds**. Mongo server CPU
was **2.570 seconds**. There was no observed pool failure or admission queue.

The historical 116.147 versus 3 x 33.738 comparison used separate runners.
Its additional 14.933 seconds cannot be attributed retrospectively to a lock.
Here, 3 x 25.970 = 77.910 versus 79.134 measured: **1.223 seconds / 1.57%**
overhead. The old 14.9-second excess was not reproduced. Small CPU/driver
scheduling increases are observed; overlapping counters cannot allocate each
residual millisecond causally.

## Low-overhead controls

| Tenants | Total wall s | Per-tenant wall s | CPU s | Peak RSS MiB | Mongo commands | Returned documents | Mongo roundtrip sum s |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 25.970 | A 25.970 | 25.411 | 111.11 | 1,464 | 182,825 | 1.118 |
| 2 | 51.992 | A 49.430; B 51.992 | 52.490 | 114.32 | 2,928 | 365,650 | 3.557 |
| 3 | 79.134 | A 73.786; B 76.592; C 79.133 | 80.036 | 117.48 | 4,392 | 548,475 | 6.820 |
| 4 | 107.164 | A 104.587; B 99.420; C 104.591; D 107.163 | 108.444 | 120.82 | 5,856 | 731,300 | 9.844 |

Documents are cumulative returned rows, including projection reads, not unique
documents or simultaneous memory residency. Per tenant: 37 find + 1,427 getMore.
No Mongo aggregate command ran on this fixture. Client roundtrip includes
driver/network/decode/scheduling, not pure server execution time.

## Three-tenant query / queue attribution (control)

| Measurement, seconds | A | B | C |
|---|---:|---:|---:|
| Event-loop CPU | 25.346 | 25.498 | 25.602 |
| find roundtrip | 0.095 | 0.044 | 0.040 |
| getMore/batch roundtrip | 2.220 | 2.172 | 2.249 |
| Pool checkout cumulative | 0.0184 | 0.0183 | 0.0274 |
| Executor queue cumulative | 0.767 | 0.720 | 0.746 |
| Worker completion to loop callback cumulative | 5.952 | 6.343 | 5.537 |
| Coordinator to factory | 0.000075 | 0.004437 | 0.007636 |
| Response JSON encoding | 0.0044 | 0.0039 | 0.0041 |

Max pool checkout: 13.29ms; max executor queue: 2.12ms. No pool/Mongo failures.
No shared spill paths. Response size ~54.6KB. Completion-dispatch intervals
include time spent executing other work on the loop and are not additive to wall.
The benchmark retains its original pool max=100; Production uses max=20.
This measures benchmark contention, not Production pool behavior.

## Stage attribution

Each cell below is **inclusive wall / exclusive CPU seconds**, from the
three-tenant instrumented sample. Nested/asynchronous wall times overlap and
must not be added. Profiling adds 23-25% overhead, so it is not substituted for
the control performance numbers.

| Stage | A | B | C |
|---|---:|---:|---:|
| Legacy summary | 68.614 / 1.713 | 65.560 / 1.709 | 64.293 / 1.734 |
| Snapshot/hydration | 30.885 / 2.032 | 26.093 / 2.037 | 22.145 / 2.039 |
| SQLite execute | 1.130 / 1.126 | 1.128 / 1.123 | 1.131 / 1.125 |
| SQLite executemany | 4.909 / 2.438 | 4.704 / 2.435 | 4.728 / 2.433 |
| Buffer encoding | 2.342 / 2.333 | 2.340 / 2.331 | 2.339 / 2.330 |
| Buffer decoding | 5.204 / 5.188 | 5.192 / 5.176 | 5.330 / 5.312 |
| Financial observe/labels | 3.439 / 3.431 | 3.432 / 3.424 | 3.502 / 3.494 |
| Fee batches | 2.476 / 2.432 | 2.486 / 2.449 | 2.493 / 2.455 |
| Financial finish | 0.008 / 0.007 | 0.009 / 0.007 | 0.009 / 0.008 |
| compute_balances | 1.000 / 0.625 | 1.003 / 0.629 | 1.000 / 0.626 |
| Python metadata accumulation | 1.476 / 1.098 | 1.483 / 1.106 | 1.487 / 1.108 |
| Operating reader | 1.379 / 0.0006 | 1.033 / 0.0006 | 2.556 / 0.0005 |
| Operating expenses | 0.042 / 0.0004 | 0.004 / 0.0004 | 0.005 / 0.0006 |
| Settlement reader | 0.082 / 0.0001 | 0.002 / 0.0001 | 0.003 / 0.0001 |
| Recurring reader | 0.029 / 0.0002 | 0.056 / 0.0002 | 2.774 / 0.0002 |
| Product calculation in summary | 26.452 / 4.371 | 22.107 / 4.401 | 25.650 / 4.438 |
| Product/catalog | 1.123 / 0.068 | 1.615 / 0.067 | 4.336 / 0.068 |
| Product page finalization | 0.826 / 0.488 | 0.825 / 0.485 | 0.817 / 0.482 |
| Executive attribution reduction | 1.819 / 1.438 | 1.839 / 1.456 | 1.853 / 1.467 |

All stages for **every tenant at N=1/2/3/4**, including currency summaries and
all inclusive/active/exclusive timings, are recorded in
[stages-all-tenants.csv](linux-forensic-bd8/stages-all-tenants.csv).
All low-overhead per-tenant metrics are in `linux-forensic-bd8/control-N.json`.

Motor `to_list` returns a Future, not an async-def. The raw profiler field
`mongo.batch_fetch` measures call/scheduling overhead only, **not await latency**.
Use getMore roundtrip plus the separately reported executor/dispatch observations
for batch timing. Pure server query-plan/lock timing was not collected.

| N | Control wall s | Profile wall s | Probe overhead |
|---:|---:|---:|---:|
| 1 | 25.970 | 32.500 | 25.1% |
| 2 | 51.992 | 64.449 | 24.0% |
| 3 | 79.134 | 98.167 | 24.1% |
| 4 | 107.164 | 131.880 | 23.1% |

## Mechanism and evidentiary limits

- Each tenant: 1,109,073 `_decode`, 279,974 `_store_value`, 155,278 SQLite
  execute and 5,548 executemany calls. Decode alone consumes ~5.2 CPU seconds
  per tenant in the profile. Repeated replay is substantial work, despite bounded RSS.
- Four tenants show uninterrupted callbacks up to 1.79s and heartbeat lag
  up to 4.90s. Cooperative yields do not parallelize synchronous Python work.
- At N=3, process read syscalls returned ~7.58GB, physical read_bytes=0,
  physical write_bytes~820MB. User CPU=75.230s; system CPU=4.806s.
  Cached spill replay/processing dominates; no evidence of physical read I/O
  being the primary delay in this run. Filesystem overhead is not zero.
- cgroup throttled_usec=0. Mongo CPU at N=1/2/3/4: 0.84/1.67/2.57/3.50s.
- Financial readers have little active work in this fixture. This is not a
  large-settlement or high-cardinality-label benchmark; do not generalize their
  low CPU to datasets not measured here.
- Admission supports four independent requests without a waiting semaphore;
  coalescing keys contain owner identity. Each request had a unique temporary
  SQLite store, and all stores were removed. No cross-tenant financial cache
  lock was identified in code or observed in these measurements.
- Financial signatures matched per tenant across controls, profiles and
  concurrency. This verifies diagnostic consistency, not a changed algorithm.

## Smallest proposed improvement -- not implemented

Target the Dashboard-private bounded replay codec first: decode at most one
cursor batch (128 records) together where the existing BSON representation
permits, avoiding repeated per-row BSON-wrapper decoding through Python.
Preserve exceptional tagged types, exact numeric representation and record
order; retain the mixed-format fallback. No full-cohort cache or monetary
calculator/rounding rewrite. This targets measured work, but speedup remains
unproven until an approved experiment and parity tests run.

If this is insufficient, identify and fuse only redundant read-only traversals
of the *same immutable cohort*, retaining each accumulator's original order.
Do not fuse differently filtered cohorts or change compute_balances semantics.
Do not add process-per-request parallelism as a shortcut that multiplies RSS.

Validation for any approved patch: exact type/value codec parity, financial
totals/signatures, current-carrier counts, tenant isolation, original ordering,
100k bounded RSS and latency at N=1/2/3/4, and event-loop lag. No fix applied.

## Status

**NOT READY**: bounded memory is retained, but 79/107s latency at three/four
tenants is still too high. This successful forensic workflow is not full release
CI. No Merge/Prepare/Prepublish/Deploy. No Production access or financial writes.
