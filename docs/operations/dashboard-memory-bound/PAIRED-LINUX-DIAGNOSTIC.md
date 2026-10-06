# P1253 paired diagnostic adaptation (smoke only)

Baseline bd8e1a4c377e61544b5cfe0f71f573c71abca89f;
candidate 15f00ff0d072cf8e00b8d642a6390d2ef71c11ac stay immutable.
No application/dependency manifests, dataset generator, financial formulas,
Production or P1253 application branch edits.

## Environment and ownership

Uses PR1276 environment contract at 2392ba6b2957cbc820cf7d806288b56589438c0b:
Ubuntu24.04 hosted runner, exact Python3.12.15 image, unchanged focused requirements,
Mongo8.0.12 actual replica set. Diagnostic image adds Git for SHA/TREE verification;
this is tooling only. Mongo and workers have no external network, no exposed port,
no Production credentials, and read-only source mounts. Database port27261 matches
the frozen benchmark. One runner, sequential workers, same image and resource settings.

## Reused measurement implementation

Uses frozen candidate benchmark_dashboard_summary_memory.py and dashboard_summary_fixture.py.
No `before` mode with its unrelated historical BASE_SHA: each target executes its
own actual application via `v2_endpoint(..., "after")`. Shared frozen generator,
SpillProbe, Reads, encoders/signatures are reused. No profiling in latency samples.
Request invocation/timing is adapted in the diagnostic wrapper only.

A is always tenant-0. Existing four-tenant generator runs once and its unchanged
records are reused for A, A+B, A+B+C, A+B+C+D and separate same-key c3/c4.
Full dataset/index fingerprints are checked after seeding and AFTER each sample,
never between cold reset and measured requests. Financial A signature is checked
across scaling; baseline/candidate financial signatures must match.

## Cold/warm and RSS

Each sample: finish previous fingerprint process -> stop Mongo -> sync -> drop
host OS caches on dedicated disposable Actions VM -> start Mongo -> primary hello
and server metadata only -> fresh worker. No data scan before cold measurement.
Cold has zero warm-up. Warm makes one identical workload request group through the
same endpoint/client in the same process, discards its payloads, then resets metrics.
Startup metadata pages can be resident; cold is not a claim that Mongo has zero RAM.

At measurement start reset Linux /proc/self/clear_refs=5; record VmHWM only through
endpoint+JSON window, before signatures/fingerprints. Includes starting process RSS,
not lifetime peak or Mongo server memory. Kernel reset failure fails closed.
Mongo command/doc/batch/duration counters exclude warm-up. Driver duration sums can
overlap and must not be added to elapsed. Cache/coalescing counters unavailable=null.

## Statistics and capacity

Contract schedule: equal repetitions, alternating AB/BA, cold/warm separate.
Statistics group exact SHA/workload/state/tenant/request index; never pool concurrent
same-key requests or different tenants as independent repetitions. Profiled observations
are rejected. p50/p95/p99 remain null/INSUFFICIENT_SAMPLES below40/400/2000 respectively.
Above those thresholds they are empirical estimates, not guaranteed confidence.

Planned full matrix: 8 workloads x2 states x2 sources x2000 repetitions =64,000
measured runs plus32,000 warm-up groups. estimate_matrix reports historical-input
capacity estimate; these are not new Linux performance evidence. Full runner is
intentionally NOT exposed/authorized. Hosted job budget must not be bypassed by
relabeling short smoke samples as performance validation.

Smoke only:12 records per tenant x4 tenants;6 scenarios x2 states x2 sources x2
repetitions =48 measured runs,24 per source. Tests validate scheduling, cold/warm,
measured counters, RSS scope, tenant identity and parity. No claim about large-data
latency/percentiles. P1253 PERFORMANCE_VALIDATION_DEFERRED; RCA UNPROVEN.

Artifacts: immutable source identities, hashes of frozen harness, runtime/image
versions/digests, package list, contract output, every sample input/result, reset
metadata, dataset/index fingerprint, measured metrics, signatures and separate
summaries. Partial failures are retained. No automatic reruns.


## Authorized one-sample capacity execution

Independent branch codex/p1253-single-capacity. Only frozen candidate is checked
out; controller has no baseline/cold/repetition loop. Unchanged four-tenant
fixture:100,000 orders each; request only tenant-0, concurrency1, warmup1 outside
timing, measured warm sample1, no profiler/percentile. Counters/timing/reset and
RSS semantics remain unchanged. Added wall timers only around unmeasured warmup
and post-window signature generation.

Capacity controller records seed, before-fingerprint, reset, whole worker,
post-fingerprint and cleanup separately. Persists phase/elapsed status. Deadline
is at most50minutes and shortened for setup elapsed, leaving evidence-upload
headroom inside60-minute job. On timeout stop only the named test worker and
fixture container; no retry. Artifacts include byte sizes. No Production access.


## Baseline capacity follow-up

Authorized branch codex/p1253-baseline-capacity executes exactly one Baseline
bd8e1a4c377e61544b5cfe0f71f573c71abca89f / A / Warm / 100k sample. Candidate
15f00ff0d072cf8e00b8d642a6390d2ef71c11ac remains the read-only harness and fixture
source, including seeding; only worker application target changes to Baseline.
No additional Candidate measurement, cold, other workload or repetition. No
application/dependency/threshold changes. Same controller, image, Mongo setup,
reset, warm-up, timing, counters and RSS semantics as Candidate run37519368225.
Compare as single-sample capacity evidence on separate hosted allocations, not
paired-run latency/RCA proof. Candidate raw peak112652288 bytes is107.4336MiB
(or112.6523 decimal MB); do not label decimal MB as MiB.


## Authorized same-host capacity pairs

Branch codex/p1253-same-host-paired: one GitHub-hosted job, one Mongo container
and dataset seeded once from the unchanged frozen candidate fixture. Twenty
sequential measurement workers: ten pairs, BC/CB alternation, same A/100k/Warm
contract. Each worker gets the same reset and one unmeasured warmup. Unchanged
p1253_paired_linux.py records RSS,CPU,Mongo,JSON and signatures. Dataset/index
fingerprint checked after each sample and before next reset. Same built image
for both source targets, same VM/runtime/resources. No other workloads or
percentiles. Ten-sample median is descriptive only; existing thresholds remain.
18 contract tests pass. Expected about30-40minutes based on capacity samples;
50minute controller deadline leaves artifact-upload headroom. No automatic
rerun. Application,dependencies,dataset,baseline,candidate unchanged.
