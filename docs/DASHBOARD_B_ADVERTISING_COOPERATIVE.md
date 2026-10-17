# Advertising cooperative experiment — PR1301

Baseline: d3b2baa1db38b2fc82e0497a637017bb0fc55376.
Experiment only; no runtime adoption. The earlier RCA is in Issue1006 comment6085419315.

## Change contract

Current advertising vs cooperative1/5/10ms, with unchanged parser5ms and cost5ms
in all arms. Private AST compilation inserts one CPU-budget checkpoint before
processing each next order. Accumulators, continue paths, currency conversion,
provider order, rounding and finalizers remain the original statements.
No runtime files, DB read sharing, cross-request cache, snapshot, governor,
Accounting, Shipping, Review or Android changes.

## Verification

Real isolated MongoDB8.0.12 replica set, Linux/Python3.11. Exact encoded JSON,
read counts, mixed filters, permissions/tenants, malformed and duplicate cases,
existing ten mutation schedules, cancellation and post-input-read mutation.
Correctness forced-checkpoint samples are never performance data.

Independent HTTP client with small-list/order/ping alongside Dashboard:
10K multi-same,100K single-same,100K multi-different. Three Dashboards per multi
wave. Eight measured waves per arm plus warmup, Williams balanced ordering,
fixed deterministic arrival offsets. Current and all candidates share unchanged
parser/cost prerequisites. This is not a comparison against deployed Production.

Report Dashboard/interactive p50/p95/p99, heartbeat, API-thread CPU, throughput,
Mongo command latency, admission/execution, errors/timeouts, serialization,RSS.
Also retain cost blocks and advertising budget slices for residual attribution.
Async whole-call CPU spans include other coroutine work: never treat them as
exclusive CPU or add overlapping phases. Heartbeat is the event-loop probe.

Selection requires exact equivalence, improved interactive tails, no material
Dashboard/throughput regression or new errors/timeouts. Use the preceding gate's
diagnostic interactive materiality (>10% AND >25ms), inspect every endpoint and
scenario, and disclose variance/sample count; do not select on Dashboard alone.
A pathological single record, GC and later serialization remain unbounded by an
outer-loop checkpoint. Any residual bottleneck is reported only, not optimized.

## Handoff

CI/results pending on the experiment HEAD. PR stays Draft. Final evidence goes
to canonical Issue1006 and the PR description after completion. No merge,
prepare, prepublish, deploy or Production test. Production writes=0.
Rollback of the experiment is removal of these new test/workflow/doc files;
production code is unchanged.
